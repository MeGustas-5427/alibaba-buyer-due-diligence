// Function payload for a permitted page-context tool, not a browser connection or login test.
() => {
  const crmPage = location.hostname === 'alicrm.alibaba.com' ||
    (location.hostname === 'i.alibaba.com' && location.pathname.startsWith('/hub/alicrm/'));
  const profilePage = location.hostname === 'profile.alibaba.com' &&
    /^\/profile\/my_?profile\.htm$/i.test(location.pathname);
  return {
    hostname: location.hostname, pathname: location.pathname,
    pageReady: ['interactive', 'complete'].includes(document.readyState),
    allowedPage: location.protocol === 'https:' && !location.port && (crmPage || profilePage),
    crmPage, profilePage,
    sessionState: 'not_verified; inspect visible login/risk state and actual collector API results'
  };
}
