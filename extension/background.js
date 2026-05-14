// background.js — MV3 service worker (minimal; API calls are made from popup directly)

chrome.runtime.onInstalled.addListener(() => {
  console.log('AutoForm AI installed');
});
