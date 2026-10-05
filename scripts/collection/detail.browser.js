(() => {
  function assert(condition, message) {
    if (!condition) throw new Error(message);
  }

  function tokenFromCookie() {
    return document.cookie
      .split('; ')
      .find((item) => item.startsWith('_tb_token_='))
      ?.split('=')[1];
  }

  function isCrmPage(hostname, pathname) {
    return hostname === 'alicrm.alibaba.com' ||
      (hostname === 'i.alibaba.com' && pathname.startsWith('/hub/alicrm/'));
  }

  function formatBeijingFromSeconds(seconds) {
    if (!seconds && seconds !== 0) return null;
    if (Number(seconds) < 0) return null;
    return new Date(Number(seconds) * 1000 + 8 * 60 * 60 * 1000)
      .toISOString()
      .slice(0, 19)
      .replace('T', ' ');
  }

  function compactRegisterDate(seconds) {
    const hidden = seconds !== undefined && seconds !== null && Number(seconds) < 0;
    return {
      rawSeconds: seconds ?? null,
      beijingTime: formatBeijingFromSeconds(seconds),
      hidden
    };
  }

  function joinPhone(phone) {
    if (!phone) return null;
    const value = [phone.countryCode || phone.mobileCountryCode, phone.areaCode, phone.number || phone.mobilePhoneNum]
      .filter(Boolean)
      .join('-');
    return value || null;
  }

  function contactName(contact) {
    return [contact.firstName, contact.lastName].filter(Boolean).join(' ') || null;
  }

  function genderName(code) {
    return { M: '男', F: '女' }[code] || null;
  }

  function currentCustomerId() {
    const url = new URL(location.href);
    return url.searchParams.get('customerId') || url.hash.match(/customerId=([^&]+)/)?.[1] || null;
  }

  function normalizeInputs(input) {
    if (!input) return [{ customerId: currentCustomerId() }];
    if (Array.isArray(input)) return input;
    if (Array.isArray(input?.json?.data?.data)) return input.json.data.data;
    if (Array.isArray(input?.data?.data)) return input.data.data;
    if (Array.isArray(input?.records)) return input.records;
    return [input];
  }

  async function getJson(url, init) {
    const response = await fetch(url, { credentials: 'include', signal: AbortSignal.timeout(30000), ...init });
    if (response.redirected || [401, 403, 429].includes(response.status)) throw new Error(`CRM authorization/risk gate: HTTP ${response.status}`);
    let json;
    try { json = await response.json(); }
    catch { throw new Error('CRM returned non-JSON data; check login/risk state before resuming.'); }
    if (!response.ok || json.success !== true) {
      throw new Error(`CRM API probe failed: HTTP ${response.status}`);
    }
    return json;
  }

  async function queryCustomerAndContacts(customerId) {
    const token = tokenFromCookie();
    if (!token) throw new Error('Missing _tb_token_; run this on alicrm.alibaba.com after login.');
    const query = new URLSearchParams({
      customerId,
      _tb_token_: token,
      __t__: String(Date.now())
    });
    return getJson(`https://alicrm.alibaba.com/eggCrmQn/crm/customerQueryServiceI/queryCustomerAndContacts.json?${query}`);
  }

  async function listNameCard(customerId, contact) {
    const token = tokenFromCookie();
    if (!contact?.id || !contact?.referenceId) return null;
    const query = new URLSearchParams({ _tb_token_: token });
    const json = await getJson(`https://alicrm.alibaba.com/eggCrmQn/crm/contactQueryServiceI/listNameCard.json?${query}`, {
      method: 'POST',
      headers: { 'content-type': 'application/json;charset=UTF-8' },
      body: JSON.stringify({
        nameCardQryList: [
          {
            referenceId: contact.referenceId,
            contactId: contact.id,
            customerId
          }
        ]
      })
    });
    return json.data?.data?.[0] || null;
  }

  function buildAddress(address) {
    if (!address) return { raw: null, text: null };
    const raw = {
      country: address.country || null,
      province: address.province || null,
      city: address.city || null,
      district: address.district || null,
      street: address.street || null
    };
    return {
      raw,
      text: [raw.province, raw.city, raw.district, raw.street].filter(Boolean).join(' ')
    };
  }

  function compactContact(contact, nameCard) {
    return {
      contactId: contact.id || null,
      referenceId: contact.referenceId || null,
      isMain: contact.isMain === 'y',
      firstName: contact.firstName || null,
      lastName: contact.lastName || null,
      contactName: contactName(contact),
      emailFull: contact.email?.[0] || nameCard?.email || null,
      emailVerified: nameCard?.isVerified ?? null,
      nameCardAuthorized: nameCard?.authorized ?? null,
      mobile: joinPhone(contact.mobiles?.[0] || nameCard?.mobiles),
      phone: joinPhone(contact.phoneNumbers?.[0] || nameCard?.phoneNumbers),
      position: contact.position || null,
      department: contact.department || null,
      gender: contact.gender || null,
      genderName: genderName(contact.gender),
      avatar: contact.avatar || null,
      buyerProfileLink: contact.profileLink || null,
      socialAccounts: contact.ims || [],
      contactMemo: contact.memo || null
    };
  }

  async function collectOne(input) {
    const customerId = input.customerId || input.detailPage?.params?.customerId;
    if (!customerId) throw new Error('Missing customerId.');

    const detailJson = await queryCustomerAndContacts(customerId);
    const detail = detailJson.data || {};
    if (!detail.customerDetailCO || typeof detail.customerDetailCO !== 'object' ||
      !Array.isArray(detail.contactQueryCOList)) throw new Error('CRM detail shape changed; collection stopped.');
    const customer = detail.customerDetailCO || {};
    const contacts = detail.contactQueryCOList || [];
    const mainContact = contacts.find((item) => item.isMain === 'y') || contacts[0] || null;
    const mainNameCard = mainContact ? await listNameCard(customerId, mainContact) : null;
    const address = buildAddress(customer.address);

    return {
      customerId,
      companyName: (customer.companyName || input.companyName || '').trim() || null,
      fieldsMissingFromList: {
        website: customer.website || null,
        registerDate: compactRegisterDate(customer.registerDate),
        fax: joinPhone(customer.fax),
        address: address.raw,
        addressText: address.text,
        ownerId: customer.owner || null,
        ownerName: customer.ownerName || null,
        customerMemo: customer.memo || null,
        contacts: mainContact ? [compactContact(mainContact, mainNameCard)] : []
      },
      sourceEndpoints: {
        detail: '/eggCrmQn/crm/customerQueryServiceI/queryCustomerAndContacts.json',
        emailVerification: '/eggCrmQn/crm/contactQueryServiceI/listNameCard.json'
      }
    };
  }

  async function collectAliCrmCustomerDetailMissingFields(input, options = {}) {
    selfCheck();
    if (!isCrmPage(location.hostname, location.pathname)) {
      throw new Error('Run this script on an Alibaba CRM page.');
    }
    const rows = normalizeInputs(input);
    const results = new Array(rows.length);
    let nextIndex = 0;
    let stopped = false;
    const workerCount = Math.max(1, Math.min(options.concurrency || 3, rows.length));

    async function worker() {
      while (!stopped && nextIndex < rows.length) {
        const index = nextIndex;
        nextIndex += 1;
        try {
          results[index] = await collectOne(rows[index]);
        } catch (error) {
          stopped = options.stopOnError !== false;
          results[index] = {
            customerId: rows[index]?.customerId || rows[index]?.detailPage?.params?.customerId || null,
            companyName: rows[index]?.companyName || null,
            error: error.message || String(error)
          };
        }
      }
    }

    await Promise.all(Array.from({ length: workerCount }, worker));
    return {
      schemaVersion: 'alicrm.customer-detail-missing-fields.v1',
      capturedAt: new Date().toISOString(),
      collector: 'collect_customer_detail_missing_fields.browser.js',
      count: results.filter(Boolean).length,
      results: results.filter(Boolean)
    };
  }

  function selfCheck() {
    assert(formatBeijingFromSeconds(1380819194) === '2013-10-04 00:53:14', 'date formatter');
    assert(formatBeijingFromSeconds(-1) === null, 'hidden date formatter');
    assert(compactRegisterDate(-1).hidden === true, 'hidden register date');
    assert(buildAddress({ country: 'SOMALIA' }).text === '', 'country is not business address');
    assert(buildAddress({ country: 'US', city: 'Miami', street: 'Ocean Dr' }).text === 'Miami Ocean Dr', 'business address text');
    assert(joinPhone({ countryCode: '1', areaCode: '202', number: '5550100' }) === '1-202-5550100', 'synthetic phone formatter');
    assert(genderName('M') === '男', 'gender map');
    assert(isCrmPage('alicrm.alibaba.com', '/'), 'legacy CRM page');
    assert(isCrmPage('i.alibaba.com', '/hub/alicrm/my_customer'), 'current CRM page');
  }

  selfCheck();
  const api = { collectAliCrmCustomerDetailMissingFields };
  if (typeof window !== 'undefined') {
    window.collectAliCrmCustomerDetailMissingFields = collectAliCrmCustomerDetailMissingFields;
    window.aliCrmCustomerDetailCollector = api;
    return 'collectAliCrmCustomerDetailMissingFields ready';
  }
  if (typeof module !== 'undefined') module.exports = api;
  return api;
})();
