#!/usr/bin/env node

const assert = require('assert');
const fs = require('fs');
const path = require('path');

const CRM_BASE_URL = 'https://alicrm.alibaba.com/';
const DETAIL_TAB_TYPE = 'customer-detail';

function readJson(filePath) {
  return JSON.parse(fs.readFileSync(filePath, 'utf8'));
}

function writeJson(filePath, value) {
  fs.writeFileSync(filePath, `${JSON.stringify(value, null, 2)}\n`, 'utf8');
}

function own(object, key) {
  return Object.prototype.hasOwnProperty.call(object || {}, key);
}

function asText(value) {
  if (value === undefined || value === null) return null;
  return String(value);
}

function asArray(value) {
  if (value === undefined || value === null) return [];
  return Array.isArray(value) ? value : [value];
}

function mapCode(dictionaries, dictionaryName, code) {
  const normalizedCode = asText(code);
  if (!normalizedCode) {
    return { code: null, name: null, resolved: false };
  }

  const dictionary = dictionaries[dictionaryName] || {};
  const resolved = own(dictionary, normalizedCode);
  return {
    code: normalizedCode,
    name: resolved ? dictionary[normalizedCode] : null,
    resolved
  };
}

function mapCodes(dictionaries, dictionaryName, codes) {
  return asArray(codes).map((code) => mapCode(dictionaries, dictionaryName, code));
}

function buildQueryString(params) {
  return Object.entries(params)
    .filter(([, value]) => value !== undefined && value !== null && value !== '')
    .map(([key, value]) => `${encodeURIComponent(key)}=${encodeURIComponent(String(value))}`)
    .join('&');
}

function normalizeBaseUrl(baseUrl) {
  const withoutHashOrQuery = String(baseUrl || CRM_BASE_URL).replace(/[?#].*$/, '');
  return withoutHashOrQuery.endsWith('/') ? withoutHashOrQuery : `${withoutHashOrQuery}/`;
}

function buildDetailHashUrl(row, baseUrl = CRM_BASE_URL) {
  const query = buildQueryString({
    customerId: row.customerId,
    companyName: row.companyName
  });
  return `${normalizeBaseUrl(baseUrl)}#${DETAIL_TAB_TYPE}?${query}`;
}

function mapMarketingGroups(dictionaries, value) {
  const values = asArray(value);
  return values.map((item) => {
    if (item && typeof item === 'object') {
      const id = asText(
        item.customerGroupId ||
          item.marketingCustomerGroupId ||
          item.groupId ||
          item.id ||
          item.value ||
          item.code
      );
      const name =
        item.name ||
        item.groupName ||
        item.label ||
        (id && dictionaries.marketingCustomerGroupId
          ? dictionaries.marketingCustomerGroupId[id]
          : null) ||
        null;

      return {
        id,
        name,
        resolved: Boolean(name)
      };
    }

    const mapped = mapCode(dictionaries, 'marketingCustomerGroupId', item);
    return {
      id: mapped.code,
      name: mapped.name,
      resolved: mapped.resolved
    };
  });
}

function summarizeMappedList(entries) {
  const names = entries.filter((entry) => entry.resolved).map((entry) => entry.name);
  const unresolvedCodes = entries
    .filter((entry) => entry.code && !entry.resolved)
    .map((entry) => entry.code);

  return { values: entries, names, unresolvedCodes };
}

function summarizeGroups(entries) {
  const names = entries.filter((entry) => entry.resolved).map((entry) => entry.name);
  const unresolvedIds = entries.filter((entry) => entry.id && !entry.resolved).map((entry) => entry.id);

  return {
    values: entries,
    names,
    unresolvedIds,
    displayName: names.length ? names.join(', ') : '暂无所属客群'
  };
}

function enrichRecentNote(dictionaries, recentNote) {
  if (!recentNote) {
    return {
      parentCode: null,
      parentName: null,
      noteCode: null,
      noteName: null,
      uiName: null,
      contentCode: null,
      content: null,
      uiContent: null,
      noteTime: null,
      displayName: null,
      displayText: null,
      raw: null
    };
  }

  const parent = mapCode(dictionaries, 'noteParentCode', recentNote.parentNoteCode);
  const note = mapCode(dictionaries, 'noteCode', recentNote.noteCode);
  const uiName =
    (recentNote.noteCode && dictionaries.noteUiName && dictionaries.noteUiName[recentNote.noteCode]) ||
    null;
  const uiContent =
    (recentNote.contentCode &&
      dictionaries.noteContentCode &&
      dictionaries.noteContentCode[recentNote.contentCode]) ||
    null;
  const displayName = uiName || note.name || recentNote.content || parent.name || null;
  const displayText = uiContent || recentNote.content || note.name || displayName;

  return {
    parentCode: parent.code,
    parentName: parent.name,
    noteCode: note.code,
    noteName: note.name,
    uiName,
    contentCode: asText(recentNote.contentCode),
    content: recentNote.content || null,
    uiContent,
    noteTime: recentNote.noteTime || null,
    displayName,
    displayText,
    raw: {
      type: recentNote.type || null,
      noteLabel: recentNote.noteLabel || null,
      noteId: recentNote.noteId || null
    }
  };
}

function unwrapListResponse(input) {
  const payload = input.json || input;
  const rows = payload && payload.data && payload.data.data;

  if (!Array.isArray(rows)) {
    throw new Error('Input JSON does not look like a queryCustomerList response.');
  }

  return {
    payload,
    rows,
    requestBody: input.requestBody || null,
    total: payload.data.total === undefined ? rows.length : payload.data.total,
    crmSourceRoleId: payload.data.crmSourceRoleId || null
  };
}

function enrichRow(row, dictionaries) {
  const contact = row.mainContact || {};
  const customerId = asText(row.customerId);
  const companyName = row.companyName || null;
  const categories = mapCodes(dictionaries, 'categoryId', row.categorys);
  const categorySummary = summarizeMappedList(categories);
  const marketingGroups = mapMarketingGroups(
    dictionaries,
    row.marketingCustomerGroups || row.marketingGroups || []
  );
  const marketingGroupSummary = summarizeGroups(marketingGroups);
  const customerSources = summarizeMappedList(
    mapCodes(dictionaries, 'customerSources', row.customerSources)
  );
  const businessTypes = summarizeMappedList(
    mapCodes(dictionaries, 'businessTypes', row.businessTypes)
  );
  const followStatus = enrichRecentNote(dictionaries, row.recentNote);
  const purchaseIntent = mapCode(dictionaries, 'importanceLevel', row.importanceLevel);
  const annualProcurement = mapCode(dictionaries, 'annualProcurement', row.annualProcurement);
  const customerStage = mapCode(dictionaries, 'customerGroup', row.customerGroup);
  const memberLevel = mapCode(dictionaries, 'memberLevel', row.memberLevel);
  const country = mapCode(dictionaries, 'countryCode', row.countryCode);

  const detailPage = {
    status: 'pending_fetch',
    tabType: DETAIL_TAB_TYPE,
    params: { customerId, companyName },
    hashUrl: buildDetailHashUrl(row),
    frontEndOpenEvent: {
      name: 'EVENT_CREATE_NEW_TAB',
      args: [DETAIL_TAB_TYPE, { customerId, companyName }, true, true]
    },
    source: 'my-customer list row click opens TAB_TYPE_CUSTOMER_DETAIL with customerId/companyName'
  };

  const customer = {
    customerId,
    companyName,
    contactName: contact.contactName || null,
    emailMasked: contact.email || null,
    fullEmail: null,
    loginId: row.loginId || contact.loginId || null,
    aliId: contact.aliId || null,
    referenceId: contact.referenceId || null,
    highQualityLevelTag:
      contact.highQualityLevelTag || contact.growthLevelInfo?.growthLevel || row.growthLevel || null
  };

  const fieldValues = {
    客户: customer,
    业务员: row.saleName || null,
    客户阶段: customerStage,
    跟进状态: {
      displayName: followStatus.displayName,
      displayText: followStatus.displayText,
      parentCode: followStatus.parentCode,
      parentName: followStatus.parentName,
      noteCode: followStatus.noteCode,
      noteName: followStatus.noteName,
      contentCode: followStatus.contentCode,
      content: followStatus.content
    },
    小记时间: followStatus.noteTime,
    采购意向: purchaseIntent,
    年采购额: annualProcurement,
    采购品类: {
      ids: categorySummary.values.map((entry) => entry.code),
      names: categorySummary.names,
      unresolvedIds: categorySummary.unresolvedCodes,
      values: categorySummary.values
    },
    '国家/地区': country,
    所属客群: marketingGroupSummary,
    客户来源: customerSources,
    商业类型: businessTypes,
    建档时间: row.createDate || null
  };

  return {
    customerId,
    companyName,
    detailPage,
    customer,
    saleName: row.saleName || null,
    customerStage,
    memberLevel,
    followStatus,
    followFlag: mapCode(
      {
        followFlag: {
          y: '是',
          n: '否'
        }
      },
      'followFlag',
      row.follow
    ),
    purchaseIntent,
    annualProcurement,
    purchaseCategories: {
      rawIds: categorySummary.values.map((entry) => entry.code),
      names: categorySummary.names,
      unresolvedIds: categorySummary.unresolvedCodes,
      values: categorySummary.values
    },
    country,
    marketingCustomerGroups: marketingGroupSummary,
    customerSources,
    businessTypes,
    createDate: row.createDate || null,
    pickUpDate: row.pickUpDate || null,
    listOnlyFields: {
      isDing: Boolean(row.isDing),
      taOrderCount: row.taOrderCount ?? null,
      willLoss: row.willLoss || null,
      repurchase: row.repurchase || null,
      originSaleName: row.originSaleName || null,
      annualProcurementOnline: row.annualProcurementOnline || null
    },
    fieldValues
  };
}

function deriveOutputPath(inputPath) {
  if (inputPath.endsWith('.wrapper.json')) {
    return inputPath.replace(/\.wrapper\.json$/, '.enriched.json');
  }
  if (inputPath.endsWith('.json')) {
    return inputPath.replace(/\.json$/, '.enriched.json');
  }
  return `${inputPath}.enriched.json`;
}

function buildEnrichedList(inputFile, dictionaryFile) {
  const input = readJson(inputFile);
  const dictionaryRoot = dictionaryFile ? readJson(dictionaryFile) : {};
  const dictionaries = dictionaryRoot.dictionaries || {};
  const list = unwrapListResponse(input);
  const records = list.rows.map((row) => enrichRow(row, dictionaries));

  return {
    schemaVersion: 'alicrm.customer-list.enriched.v1',
    generatedAt: new Date().toISOString(),
    source: {
      inputFile,
      dictionaryFile,
      requestBody: list.requestBody,
      success: list.payload.success ?? null,
      message: list.payload.message ?? null,
      code: list.payload.code ?? null,
      totalFromResponse: list.total,
      recordCount: records.length,
      crmSourceRoleId: list.crmSourceRoleId
    },
    fieldMapping: {
      客户: 'companyName + mainContact.contactName/mainContact.email/customerId',
      业务员: 'saleName',
      客户阶段: 'customerGroup -> dictionaries.customerGroup',
      跟进状态: 'recentNote.parentNoteCode/recentNote.noteCode/recentNote.contentCode',
      小记时间: 'recentNote.noteTime',
      采购意向: 'importanceLevel -> dictionaries.importanceLevel',
      年采购额: 'annualProcurement -> dictionaries.annualProcurement',
      采购品类: 'categorys[] -> dictionaries.categoryId',
      '国家/地区': 'countryCode -> dictionaries.countryCode',
      所属客群: 'marketingCustomerGroups[]/marketingGroups -> dictionaries.marketingCustomerGroupId',
      客户来源: 'customerSources[] -> dictionaries.customerSources',
      商业类型: 'businessTypes[] -> dictionaries.businessTypes',
      建档时间: 'createDate'
    },
    detailPageCollectionQueue: records.map((record) => ({
      customerId: record.customerId,
      companyName: record.companyName,
      status: record.detailPage.status,
      tabType: record.detailPage.tabType,
      params: record.detailPage.params,
      hashUrl: record.detailPage.hashUrl
    })),
    records
  };
}

function runSelfChecks() {
  assert.strictEqual(
    buildDetailHashUrl({ customerId: 'abc', companyName: 'a b' }),
    'https://alicrm.alibaba.com/#customer-detail?customerId=abc&companyName=a%20b'
  );

  const dictionaries = {
    categoryId: { '1': 'Category One' },
    countryCode: { CO: '哥伦比亚' },
    customerGroup: { 0: '询盘客户' },
    importanceLevel: { 0: '未设置' },
    annualProcurement: { NA: 'NA' },
    noteParentCode: { negotiating: '洽谈中' },
    noteCode: { auto_note_tm: '主动发起IM沟通' },
    noteUiName: { auto_note_tm: 'TM沟通' },
    noteContentCode: { 'alicrm.auto_note_tm_contentCode': '商家主动发起的tm' }
  };
  const record = enrichRow(
    {
      customerId: 'c1',
      companyName: 'SYNTHETIC Company',
      customerGroup: '0',
      importanceLevel: '0',
      annualProcurement: 'NA',
      countryCode: 'CO',
      categorys: ['1', '2'],
      recentNote: {
        parentNoteCode: 'negotiating',
        noteCode: 'auto_note_tm',
        contentCode: 'alicrm.auto_note_tm_contentCode',
        content: '主动发起IM沟通',
        noteTime: '2026-07-05T16:46:38.000Z'
      }
    },
    dictionaries
  );

  assert.strictEqual(record.customerStage.name, '询盘客户');
  assert.strictEqual(record.country.name, '哥伦比亚');
  assert.strictEqual(record.followStatus.displayName, 'TM沟通');
  assert.strictEqual(record.followStatus.displayText, '商家主动发起的tm');
  assert.deepStrictEqual(record.purchaseCategories.unresolvedIds, ['2']);
}

module.exports = {buildEnrichedList, enrichRow, runSelfChecks};
