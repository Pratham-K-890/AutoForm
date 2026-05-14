// content.js — injected into Google Forms pages

chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.action === 'ping') {
    sendResponse({ ready: true });
    return true;
  }
  if (request.action === 'debug') {
    try {
      sendResponse({ success: true, report: runDiagnostics() });
    } catch (e) {
      sendResponse({ success: false, error: e.message });
    }
    return true;
  }
  if (request.action === 'scrapeQuestions') {
    try {
      sendResponse({ success: true, ...scrapeQuestions() });
    } catch (e) {
      sendResponse({ success: false, error: e.message });
    }
    return true;
  }
  if (request.action === 'fillAnswers') {
    fillAnswers(request.answers, request.answerIndices || {})
      .then(() => sendResponse({ success: true }))
      .catch(e => sendResponse({ success: false, error: e.message }));
    return true;
  }
});

// ── DOM helpers ────────────────────────────────────────────────────────────────

function getQuestionBlocks() {
  // Qr7Oae is the current Google Forms question container class
  let blocks = document.querySelectorAll('div.Qr7Oae');
  if (!blocks.length) blocks = document.querySelectorAll('div.freebirdFormviewerViewItemsItemItem');
  if (!blocks.length) blocks = document.querySelectorAll('div[data-item-id]');
  return Array.from(blocks).filter(b => getQuestionText(b));
}

function getQuestionText(block) {
  const selectors = [
    'span.M7eMe',
    '[role="heading"] span',
    '.freebirdFormviewerViewItemsItemItemTitle span',
    'div.freebirdFormviewerViewItemsItemItemTitle',
  ];
  for (const sel of selectors) {
    const el = block.querySelector(sel);
    if (!el) continue;
    // Use only direct text nodes to avoid pulling in child element text (e.g. "Options:")
    const directText = Array.from(el.childNodes)
      .filter(n => n.nodeType === Node.TEXT_NODE)
      .map(n => n.textContent.trim())
      .join(' ')
      .replace(/\*/g, '')
      .trim();
    if (directText) return directText;
    // Fall back to full textContent if no direct text nodes found
    const full = el.textContent.replace(/\*/g, '').trim();
    if (full) return full;
  }
  return '';
}

function detectType(block) {
  if (block.querySelector('[role="radiogroup"]')) return 'mcq';
  if (block.querySelector('[role="group"] [role="checkbox"]')) return 'checkbox';
  if (block.querySelector('[role="listbox"]') || block.querySelector('.quantumWizMenuPaperselectContent')) return 'dropdown';
  if (block.querySelector('textarea')) return 'paragraph';
  if (block.querySelector('input[type="date"]')) return 'date';
  if (block.querySelector('input[type="time"]')) return 'time';
  return 'short_text';
}

// Try multiple strategies to find the label text for a radio/checkbox item
function getOptionText(el) {
  // 1. aria-label on the element itself (Google Forms sets this for accessibility)
  const ariaLabel = el.getAttribute('aria-label');
  if (ariaLabel?.trim()) return ariaLabel.trim();

  // 2. data-value attribute
  const dataValue = el.getAttribute('data-value');
  if (dataValue?.trim()) return dataValue.trim();

  // 3. Known child class selectors
  const candidates = ['.nWQGrd', '.vd3tt', 'span[dir="auto"]', 'span[dir]'];
  for (const sel of candidates) {
    const found = el.querySelector(sel);
    if (found) {
      const t = found.textContent.trim();
      if (t) return t;
    }
  }

  // 4. aria-label on a sibling element (label next to the radio circle)
  const sibling = el.nextElementSibling;
  if (sibling) {
    const t = sibling.textContent.trim();
    if (t) return t;
  }

  // 5. Direct text nodes
  return Array.from(el.childNodes)
    .filter(n => n.nodeType === Node.TEXT_NODE)
    .map(n => n.textContent.trim())
    .join(' ')
    .trim();
}

function getOptions(block, type) {
  if (type === 'mcq') {
    return Array.from(block.querySelectorAll('[role="radio"]'))
      .map(getOptionText)
      .filter(Boolean);
  }
  if (type === 'checkbox') {
    return Array.from(block.querySelectorAll('[role="checkbox"]'))
      .map(getOptionText)
      .filter(Boolean);
  }
  if (type === 'dropdown') {
    return Array.from(block.querySelectorAll('[role="option"]'))
      .map(o => o.textContent.trim())
      .filter(t => t && t !== 'Choose');
  }
  return [];
}

// ── Scrape ─────────────────────────────────────────────────────────────────────

function getQuestionImage(block) {
  // Look for images that are part of the question (not UI icons)
  const img = block.querySelector('img[src]:not([role="presentation"]):not([aria-hidden="true"])');
  if (!img) return null;
  const src = img.src || img.getAttribute('src');
  // Skip tiny icons (width/height <= 24px) and data URIs that are just UI elements
  if (!src || src.startsWith('data:') ) return null;
  if (img.width > 0 && img.width <= 24) return null;
  return src;
}

function scrapeQuestions() {
  const blocks = getQuestionBlocks();
  const formTitle =
    document.querySelector('div.freebirdFormviewerViewHeaderTitle')?.textContent?.trim() ||
    document.title.replace(' - Google Forms', '').trim();

  const questions = blocks.map((block, i) => {
    const text = getQuestionText(block);
    const type = detectType(block);
    return {
      id: `q_${i}`,
      text,
      type,
      options: getOptions(block, type),
      required: !!(block.querySelector('span.vnumgf') || block.querySelector('[aria-required="true"]')),
      entry_id: null,
      image_url: getQuestionImage(block),
    };
  });

  return { questions, form_title: formTitle, form_type: 'general' };
}

// ── Fill ───────────────────────────────────────────────────────────────────────

function norm(s) {
  return String(s).toLowerCase().replace(/\s+/g, ' ').trim();
}


// Invoke React's own onClick/onMouseDown handler directly via the fiber.
// This is necessary because Google Forms ignores synthetic DOM events —
// it processes clicks through React's internal event system.
function triggerReact(el) {
  const fiberKey = Object.keys(el).find(k =>
    k.startsWith('__reactFiber') || k.startsWith('__reactInternalInstance')
  );
  if (!fiberKey) return false;

  const fakeEvt = {
    type: 'click', target: el, currentTarget: el,
    bubbles: true, cancelable: true,
    preventDefault: () => {}, stopPropagation: () => {},
    nativeEvent: { stopImmediatePropagation: () => {} },
  };

  let fiber = el[fiberKey];
  while (fiber) {
    const props = fiber.memoizedProps;
    if (props) {
      if (props.onClick)     { props.onClick(fakeEvt);     return true; }
      if (props.onMouseDown) { props.onMouseDown(fakeEvt); return true; }
    }
    fiber = fiber.return;
  }
  return false;
}

// Fall back to full DOM event sequence if React fiber is unavailable
function simulateClick(el) {
  if (triggerReact(el)) return;
  const opts = { bubbles: true, cancelable: true, view: window };
  ['pointerdown', 'pointerup'].forEach(t =>
    el.dispatchEvent(new PointerEvent(t, opts))
  );
  ['mousedown', 'mouseup', 'click'].forEach(t =>
    el.dispatchEvent(new MouseEvent(t, opts))
  );
}

function setNativeValue(element, value) {
  const proto = element.tagName === 'TEXTAREA'
    ? window.HTMLTextAreaElement.prototype
    : window.HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
  if (setter) setter.call(element, value);
  else element.value = value;
  element.dispatchEvent(new Event('input', { bubbles: true }));
  element.dispatchEvent(new Event('change', { bubbles: true }));
}

function fillBlock(block, answer, type, idx) {
  if (type === 'short_text') {
    const input = block.querySelector('input.whsOnd, input[type="text"]');
    if (input) setNativeValue(input, String(answer));
    return;
  }
  if (type === 'paragraph') {
    const ta = block.querySelector('textarea');
    if (ta) setNativeValue(ta, String(answer));
    return;
  }
  if (type === 'date') {
    const input = block.querySelector('input[type="date"]');
    if (input) setNativeValue(input, String(answer));
    return;
  }
  if (type === 'time') {
    const input = block.querySelector('input[type="time"]');
    if (input) setNativeValue(input, String(answer));
    return;
  }
  if (type === 'mcq') {
    const radios = Array.from(block.querySelectorAll('[role="radio"]'));
    const target = (idx !== undefined && radios[idx]) ? radios[idx] : (() => {
      const a = norm(String(answer));
      return radios.find(r => norm(getOptionText(r)) === a) ||
             radios.find(r => norm(getOptionText(r)).includes(a)) ||
             radios.find(r => a.includes(norm(getOptionText(r))));
    })();
    if (target) {
      target.scrollIntoView({ block: 'center' });
      simulateClick(target);
      if (target.getAttribute('aria-checked') !== 'true') {
        const inner = target.querySelector('.nWQGrd, .vd3tt, span[dir="auto"]');
        if (inner) simulateClick(inner);
      }
    }
    return;
  }
  if (type === 'checkbox') {
    const cbs = Array.from(block.querySelectorAll('[role="checkbox"]'));
    const indices = Array.isArray(idx) ? idx : null;
    cbs.forEach((cb, i) => {
      const shouldCheck = indices
        ? indices.includes(i)
        : (() => {
            const answers_arr = (Array.isArray(answer) ? answer : [String(answer)]).map(norm);
            const o = norm(getOptionText(cb));
            return answers_arr.some(a => a === o || o.includes(a) || a.includes(o));
          })();
      if (shouldCheck && cb.getAttribute('aria-checked') !== 'true') {
        cb.scrollIntoView({ block: 'center' });
        simulateClick(cb);
        if (cb.getAttribute('aria-checked') !== 'true') {
          const inner = cb.querySelector('.nWQGrd, .vd3tt, span[dir="auto"]');
          if (inner) simulateClick(inner);
        }
      }
    });
    return;
  }
  if (type === 'dropdown') {
    const trigger = block.querySelector('.quantumWizMenuPaperselectContent, [role="combobox"]');
    if (trigger) {
      trigger.click();
      setTimeout(() => {
        document.querySelectorAll('[role="option"]').forEach(opt => {
          if (opt.textContent.trim().toLowerCase() === String(answer).toLowerCase()) {
            opt.click();
          }
        });
      }, 300);
    }
  }
}

async function fillAnswers(answers, answerIndices) {
  const blocks = getQuestionBlocks();
  for (let i = 0; i < blocks.length; i++) {
    const qId = `q_${i}`;
    const answer = answers[qId];
    if (answer === undefined || answer === null || answer === '') continue;
    const type = detectType(blocks[i]);
    const idx = answerIndices?.[qId];  // option index from popup, undefined for text questions
    fillBlock(blocks[i], answer, type, idx);
    if (type === 'mcq' || type === 'checkbox' || type === 'dropdown') {
      await new Promise(r => setTimeout(r, 120));
    }
  }
}

// ── Diagnostics ────────────────────────────────────────────────────────────────

function runDiagnostics() {
  const CONTAINER_SELECTORS = [
    'div.Qr7Oae',
    'div.freebirdFormviewerViewItemsItemItem',
    'div[data-item-id]',
  ];
  const TITLE_SELECTORS = [
    'span.M7eMe',
    '.freebirdFormviewerViewItemsItemItemTitle span',
    '[role="heading"] span',
    'div.freebirdFormviewerViewItemsItemItemTitle',
  ];

  // Test each container selector
  const containerResults = CONTAINER_SELECTORS.map(sel => ({
    selector: sel,
    count: document.querySelectorAll(sel).length,
  }));

  // Pick the winning container selector
  const winner = containerResults.find(r => r.count > 0);
  const blocks = winner
    ? Array.from(document.querySelectorAll(winner.selector))
    : [];

  // Test title selectors on first block
  const titleResults = blocks.length
    ? TITLE_SELECTORS.map(sel => ({
        selector: sel,
        found: !!blocks[0].querySelector(sel),
        text: blocks[0].querySelector(sel)?.textContent?.trim()?.slice(0, 60) || '',
      }))
    : [];

  // Find first MCQ block for radio HTML inspection
  const firstMcqBlock = blocks.find(b => b.querySelector('[role="radio"]'));
  const firstRadio = firstMcqBlock?.querySelector('[role="radio"]');
  const radioInnerHTML = firstRadio
    ? firstRadio.innerHTML.replace(/\s+/g, ' ').slice(0, 400)
    : null;
  const radioOuterAttrs = firstRadio
    ? { aria_label: firstRadio.getAttribute('aria-label'), data_value: firstRadio.getAttribute('data-value'), aria_checked: firstRadio.getAttribute('aria-checked') }
    : null;
  const radioClasses = firstRadio
    ? Array.from(firstRadio.querySelectorAll('*')).map(el => el.className).filter(Boolean)
    : [];

  // Sample first 5 questions
  const sample = blocks.slice(0, 5).map((block, i) => {
    const text = getQuestionText(block);
    const type = detectType(block);
    const options = getOptions(block, type);
    return {
      index: i,
      text: text.slice(0, 80),
      type,
      options: options.slice(0, 4),
      has_text_input: !!block.querySelector('input.whsOnd, input[type="text"]'),
      has_textarea: !!block.querySelector('textarea'),
      has_radio: !!block.querySelector('[role="radio"]'),
      has_checkbox: !!block.querySelector('[role="checkbox"]'),
    };
  });

  return {
    url: location.href,
    form_title: document.querySelector('div.freebirdFormviewerViewHeaderTitle')?.textContent?.trim() || document.title,
    container_selectors: containerResults,
    winning_container: winner?.selector || null,
    total_blocks: blocks.length,
    title_selectors: titleResults,
    sample_questions: sample,
    radio_debug: {
      inner_html: radioInnerHTML,
      outer_attrs: radioOuterAttrs,
      all_child_classes: [...new Set(radioClasses)].slice(0, 20),
    },
  };
}
