(() => {
  const SCHEMA_VERSION = 'alicrm.buyer-profile-fields.v1';
  const BUSINESS_TYPE_BY_ID = Object.freeze({
    2001: '工厂',
    2002: '贸易公司',
    2003: '批发/分销商',
    2004: '线下零售商',
    2005: '买办/采购代理',
    2006: '线上零售商',
    2007: '散客',
    2008: '其他'
  });
  const BUSINESS_TYPE_BY_MCMS_KEY = Object.freeze({
    'businessType@manufacturerOrFactory': '工厂',
    'businessType@tradingCompany': '贸易公司',
    'businessType@distributorWholesaler': '批发/分销商',
    'businessType@retailer': '线下零售商',
    'businessType@buyerOffice': '买办/采购代理',
    'businessType@onlineShop': '线上零售商',
    'businessType@soho': '散客',
    'businessType@other': '其他'
  });

  function assert(condition, message) {
    if (!condition) throw new Error(message);
  }

  function normalizeProfileUrl(value) {
    if (!value) return null;
    if (typeof value !== 'string') throw new Error('Profile locator must be a string.');
    const text = String(value).trim();
    const withProtocol = text.startsWith('//')
      ? `https:${text}`
      : text.startsWith('profile.alibaba.com/')
        ? `https://${text}`
        : text;
    const url = new URL(withProtocol);
    if (url.hostname !== 'profile.alibaba.com') throw new Error(`Not a profile.alibaba.com URL: ${url.hostname}`);
    if (!/^\/profile\/my_?profile\.htm$/i.test(url.pathname)) throw new Error(`Unexpected buyer profile path: ${url.pathname}`);
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.port) {
      throw new Error('Unexpected profile URL protocol, credentials, or port.');
    }
    url.protocol = 'https:';
    return url.href;
  }

  function scriptsFromHtml(html) {
    return Array.from(html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/gi), (match) => match[1]);
  }

  function parsePageData(html) {
    const script = scriptsFromHtml(html).find((item) => item.includes('window._PAGE_DATA'));
    if (!script) throw new Error('Missing window._PAGE_DATA in buyer profile page.');
    const assignment = /window\._PAGE_DATA\s*=\s*/.exec(script);
    if (!assignment) throw new Error('Missing profile data assignment.');
    const text = script.slice(assignment.index + assignment[0].length);
    if (!text.startsWith('{')) throw new Error('Profile data must be JSON.');
    let depth = 0, quoted = false, escaped = false;
    for (let i = 0; i < text.length; i++) {
      const char = text[i];
      if (quoted) {
        if (escaped) escaped = false;
        else if (char.charCodeAt(0) === 92) escaped = true;
        else if (char === '"') quoted = false;
      } else if (char === '"') quoted = true;
      else if (char === '{' || char === '[') depth++;
      else if (char === '}' || char === ']') {
        if (--depth === 0) {
          const value = JSON.parse(text.slice(0, i+1));
          if (!value.personalInfo || typeof value.personalInfo !== 'object' ||
            !value.companyInfo || typeof value.companyInfo !== 'object')
            throw new Error('Profile data shape changed.');
          return value;
        }
      }
    }
    throw new Error('Truncated profile JSON.');
  }

  function clean(value) {
    if (value === undefined || value === null) return null;
    const text = String(value).trim();
    return text && text !== '-' ? text : null;
  }

  function cleanNumber(value) {
    return typeof value === 'number' && Number.isFinite(value) ? value : null;
  }

  function optionLabel(pageData, option, labelsById = {}, labelsByKey = {}) {
    if (!option) return null;
    if (option.mcmsKey && pageData.i18nText?.[option.mcmsKey]) {
      return pageData.i18nText[option.mcmsKey];
    }
    if (option.mcmsKey && labelsByKey[option.mcmsKey]) {
      return labelsByKey[option.mcmsKey];
    }
    const id = option.id === undefined || option.id === null ? null : String(option.id);
    return id ? labelsById[id] || null : null;
  }

  function optionValue(pageData, option, labelsById = {}, labelsByKey = {}) {
    if (!option) return { id: null, mcmsKey: null, label: null, other: null };
    return {
      id: option.id ?? null,
      mcmsKey: option.mcmsKey ?? null,
      label: optionLabel(pageData, option, labelsById, labelsByKey),
      other: option.other ?? null
    };
  }

  function addressText(address) {
    if (!address) return null;
    return [address.street, address.city, address.province, address.region].map(clean).filter(Boolean).join(', ') || null;
  }

  function normalizeRow(input) {
    const contacts = input.fieldsMissingFromList?.contacts || input.contacts || [];
    const contact = contacts.find((item) => item?.buyerProfileLink) || {};
    return {
      customerId: input.customerId || null,
      companyName: input.companyName || input.fieldsMissingFromList?.companyName || null,
      buyerProfileLink: input.buyerProfileLink || input.profileUrl || contact.buyerProfileLink || null
    };
  }

  function compactProfile(row, pageData) {
    const personal = pageData.personalInfo || {};
    const company = pageData.companyInfo || {};
    const business = pageData.businessInfo || {};
    const purchase = pageData.purchaseInfo || {};
    const behavior = pageData.behaviorInfo || {};
    const transaction = pageData.transactionInfo || {};
    const businessTypes = Array.isArray(business.businessTypes) ? business.businessTypes : [];

    return {
      schemaVersion: SCHEMA_VERSION,
      customerId: row.customerId,
      companyName: row.companyName || clean(company.compName),
      buyerProfileLink: row.buyerProfileLink,
      profileUrl: row.buyerProfileLink,
      source: {
        page: '/profile/myprofile.htm',
        dataObject: 'window._PAGE_DATA',
        role: pageData.role || null,
        locale: pageData.locale || null,
        visibleSections: {
          contactInfo: Boolean(pageData.showContactInfo),
          basicInfo: Boolean(pageData.showBasicInfo),
          sourcingInfo: Boolean(pageData.showSourcingInfo),
          activitySummary: Boolean(pageData.showActivitySummary),
          transaction: Boolean(pageData.showTransaction)
        }
      },
      buyerProfile: {
        displayName: [personal.firstName, personal.lastName].map(clean).filter(Boolean).join(' ') || null,
        firstName: clean(personal.firstName),
        lastName: clean(personal.lastName),
        memberId: clean(personal.loginId),
        registeredCountryRegion: clean(personal.contactAddress?.region),
        registerYear: cleanNumber(personal.registerYear),
        maskedEmail: clean(personal.email),
        emailVerified: personal.emailVerify ?? null,
        phoneNumber: clean(personal.phoneNumber || personal.mobile || personal.telephone),
        contactAddress: {
          countryRegion: clean(personal.contactAddress?.region),
          province: clean(personal.contactAddress?.province),
          city: clean(personal.contactAddress?.city),
          street: clean(personal.contactAddress?.street),
          zip: clean(personal.contactAddress?.zip),
          addressText: addressText(personal.contactAddress)
        }
      },
      enterpriseInfo: {
        businessTypes: businessTypes.map((item) => optionValue(pageData, item, BUSINESS_TYPE_BY_ID, BUSINESS_TYPE_BY_MCMS_KEY)),
        businessTypeText: businessTypes.map((item) => optionLabel(pageData, item, BUSINESS_TYPE_BY_ID, BUSINESS_TYPE_BY_MCMS_KEY) || item.mcmsKey || item.id).filter(Boolean).join(', ') || null,
        companyName: clean(company.compName),
        address: {
          street: clean(company.regAddress?.street),
          city: clean(company.regAddress?.city),
          province: clean(company.regAddress?.province),
          region: clean(company.regAddress?.region),
          zip: clean(company.regAddress?.zip),
          addressText: addressText(company.regAddress)
        },
        position: clean(company.position),
        officialWebsite: clean(company.homepage || company.website),
        miniSiteUrl: clean(company.miniSiteUrl),
        salesPlatform: clean(company.salesPlatform),
        yearEstablished: company.yearEstablished && company.yearEstablished > 0 ? company.yearEstablished : null,
        yearEstablishedRaw: company.yearEstablished ?? null,
        employeeTotal: clean(company.employeeTotal || company.employeeCount || company.totalEmployees),
        aboutUs: clean(company.aboutUs || company.introduction),
        verified: company.verified ?? null
      },
      purchasePreference: {
        industryPreference: Array.isArray(purchase.sourcingIndustries) ? purchase.sourcingIndustries : [],
        purchaseFrequency: optionValue(pageData, purchase.purchaseFrequency),
        annualSpend: optionValue(pageData, purchase.annualPurchaseVolume)
      },
      activity90d: {
        loginDays: cleanNumber(behavior.loginDays),
        productViewCount: cleanNumber(behavior.productViewCount),
        searchCount: cleanNumber(behavior.searchCount),
        validInquiryCount: cleanNumber(behavior.validInquiryCount),
        repliedInquiryCount: cleanNumber(behavior.repliedInquiryCount),
        spamInquiryMarkedBySupplierCount: cleanNumber(behavior.spamInquiryMarkedBySupplierCount),
        validRfqCount: cleanNumber(behavior.validRfqCount),
        quotationReceivedCount: cleanNumber(behavior.quotationReceivedCount),
        quotationReadCount: cleanNumber(behavior.quotationReadCount),
        addedToContactCount: cleanNumber(behavior.addedToContactCount),
        addedToBlacklistCount: cleanNumber(behavior.addedToBlacklistCount),
        recentSearches: Array.isArray(behavior.recentSearches) ? behavior.recentSearches : [],
        sourcingIndustries: Array.isArray(behavior.sourcingIndustries) ? behavior.sourcingIndustries : [],
        latestRFQS: Array.isArray(behavior.latestRFQS) ? behavior.latestRFQS : [],
        latestInquiries: Array.isArray(behavior.latestInquiries) ? behavior.latestInquiries : []
      },
      onlineTrade: {
        visible: Boolean(pageData.showTransaction),
        hidden: !pageData.showTransaction,
        totalOrderCount: cleanNumber(transaction.totalOrderCount),
        totalOrderVolumeUsd: cleanNumber(transaction.totalOrderVolume)
      }
    };
  }

  async function collectOne(input) {
    const row = normalizeRow(input);
    if (!row.buyerProfileLink) throw new Error('Missing buyerProfileLink.');
    const profileUrl = normalizeProfileUrl(row.buyerProfileLink);
    const response = await fetch(profileUrl, { credentials: 'include', signal: AbortSignal.timeout(30000) });
    const html = await response.text();
    if (!response.ok) throw new Error(`Buyer profile request failed: HTTP ${response.status}`);
    normalizeProfileUrl(response.url || profileUrl); // Reject login/risk redirects before parsing.
    let data;
    try { data = parsePageData(html); }
    catch { throw new Error('Buyer profile data unavailable; check login/risk state and page format before resuming.'); }
    return compactProfile(row, data);
  }

  function normalizeInputs(input) {
    if (Array.isArray(input)) return input;
    if (Array.isArray(input?.records)) return input.records;
    return [input];
  }

  async function collectAliBuyerProfileFields(input, options = {}) {
    selfCheck();
    if (location.hostname !== 'profile.alibaba.com') {
      throw new Error('Run this script on profile.alibaba.com.');
    }
    const rows = normalizeInputs(input);
    const results = new Array(rows.length);
    let nextIndex = 0;
    let stopped = false;
    const workerCount = Math.max(1, Math.min(options.concurrency || 2, rows.length));

    async function worker() {
      while (!stopped && nextIndex < rows.length) {
        const index = nextIndex;
        nextIndex += 1;
        try {
          results[index] = await collectOne(rows[index]);
        } catch (error) {
          stopped = options.stopOnError !== false;
          const row = normalizeRow(rows[index] || {});
          results[index] = {
            customerId: row.customerId,
            companyName: row.companyName,
            buyerProfileLink: row.buyerProfileLink,
            error: error.message || String(error)
          };
        }
      }
    }

    await Promise.all(Array.from({ length: workerCount }, worker));
    return {
      schemaVersion: SCHEMA_VERSION,
      capturedAt: new Date().toISOString(),
      collector: 'collect_buyer_profile_fields.browser.js',
      count: results.filter(Boolean).length,
      results: results.filter(Boolean)
    };
  }

  function selfCheck() {
    const html = '<html><script>window._PAGE_DATA = '+JSON.stringify({i18nText:{}, showTransaction: false, personalInfo:{firstName:"A", lastName:"B", loginId:"m1", contactAddress:{region:"SO"}, registerYear:2014}, companyInfo:{compName:"SYNTHETIC Company", regAddress:{street:"Synthetic Street", city:"Test City", province:"Test Region", region:"SO", zip:"00000"}, yearEstablished:0}, businessInfo:{businessTypes:[{id:2008, mcmsKey:null, other:false}]}, behaviorInfo:{loginDays:25}, purchaseInfo:{annualPurchaseVolume:{id:4003, mcmsKey:"purchaseVolume@between100001And500000", other:false}}})+';</script></html>';
    const data = parsePageData(html);
    const row = { customerId: 'c1', companyName: 'ACME', buyerProfileLink: 'profile.alibaba.com/profile/my_profile.htm?m=x' };
    const profile = compactProfile(row, data);
    assert(profile.enterpriseInfo.address.addressText === 'Synthetic Street, Test City, Test Region, SO', 'company address text');
    assert(profile.buyerProfile.memberId === 'm1', 'member id');
    assert(profile.enterpriseInfo.businessTypes[0].label === '其他', 'business type id label');
    assert(profile.enterpriseInfo.businessTypeText === '其他', 'business type text');
    assert(profile.enterpriseInfo.yearEstablished === null, 'empty year established');
    assert(profile.onlineTrade.hidden === true, 'hidden transaction');
    assert(normalizeProfileUrl('//profile.alibaba.com/profile/myprofile.htm?m=x') === 'https://profile.alibaba.com/profile/myprofile.htm?m=x', 'protocol-relative profile URL');
    assert(normalizeProfileUrl('http://profile.alibaba.com/profile/myprofile.htm?m=x') === 'https://profile.alibaba.com/profile/myprofile.htm?m=x', 'upgrade profile URL to HTTPS');
    let rejectedHost = false;
    try {
      normalizeProfileUrl('https://profile.alibaba.com.evil.example/profile/myprofile.htm?m=x');
    } catch {
      rejectedHost = true;
    }
    assert(rejectedHost, 'reject lookalike profile host');
  }

  selfCheck();
  const api = { collectAliBuyerProfileFields, normalizeProfileUrl, compactProfile, parsePageData };
  if (typeof window !== 'undefined') {
    window.collectAliBuyerProfileFields = collectAliBuyerProfileFields;
    window.aliBuyerProfileCollector = api;
    return 'collectAliBuyerProfileFields ready';
  }
  if (typeof module !== 'undefined') module.exports = api;
  return api;
})();
