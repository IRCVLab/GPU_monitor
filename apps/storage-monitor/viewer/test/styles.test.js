import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
const here = path.resolve(import.meta.dirname,'..');
function cssBlock(css, selector) {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const match = css.match(new RegExp(escaped + "\\s*\\{([\\s\\S]*?)\\}"));
  assert(match, selector + ' token block must exist');
  return match[1];
}
function cssVar(block, name) {
  const match = block.match(new RegExp('--' + name + ':\\s*([^;]+);'));
  assert(match, '--' + name + ' must be present');
  return match[1].trim();
}
function testApprovedCleanThemeTokenContract() {
  const css = fs.readFileSync(path.join(here, 'styles.css'), 'utf8');
  const dark = cssBlock(css, 'html.dark');
  const light = cssBlock(css, 'html.light');
  const expectedDark = {
    bg: '#090b0f',
    surface: '#13161b',
    surface2: '#181b1f',
    separator: '#26292e',
    text: '#f0f2f4',
    text2: '#8f9aa4',
    accent: '#3a8cff',
    ok: '#00b793',
    warn: '#ff7527',
    crit: '#ff515a',
  };
  const expectedLight = {
    bg: '#f4f5f7',
    surface: '#ffffff',
    surface2: '#eceff1',
    separator: '#dbdee2',
    text: '#0c121a',
    text2: '#565e69',
    accent: '#297cef',
    ok: '#00a381',
    warn: '#f3680f',
    crit: '#ee343b',
  };
  for (const [name, value] of Object.entries(expectedDark)) {
    assert.strictEqual(cssVar(dark, name), value, 'dark Clean token --' + name + ' must match the approved contract exactly');
  }
  for (const [name, value] of Object.entries(expectedLight)) {
    assert.strictEqual(cssVar(light, name), value, 'light Clean token --' + name + ' must match the approved contract exactly');
  }
}
test('testApprovedCleanThemeTokenContract',testApprovedCleanThemeTokenContract);
function testDetailCapacityResponsiveCssContract() {
  const css = fs.readFileSync(path.join(here, 'styles.css'), 'utf8');
  assert(/\.detail-capacity-row\b[\s\S]*min-width:\s*0/.test(css), 'detail capacity rows must allow grid children to shrink without forcing horizontal overflow');
  assert(/\.cap-main\b[\s\S]*flex-wrap:\s*wrap/.test(css), 'detail capacity path/filesystem/media group must wrap instead of clipping on narrow screens');
  assert(/\.cap-bar\b[\s\S]*min-width:\s*[1-9][0-9]*px/.test(css), 'detail utilization bar must keep a visible minimum width when rows collapse');
  assert(/@media\s*\(max-width:\s*760px\)[\s\S]*\.detail-capacity-row\b[\s\S]*grid-template-columns:\s*minmax\(0,\s*1fr\)\s+auto/.test(css), '760px detail capacity rows must switch to a shrinkable two-column layout');
  assert(/@media\s*\(max-width:\s*760px\)[\s\S]*\.detail-capacity-row\s*>\s*\*\s*\{[^}]*min-width:\s*0/.test(css), '760px detail capacity row children must explicitly set min-width:0');
  assert(/@media\s*\(max-width:\s*760px\)[\s\S]*\.cap-bar\b[\s\S]*grid-column:\s*1\s*\/\s*-1/.test(css), '760px detail utilization bar must span the full row to remain visible');
  assert(/@media\s*\(max-width:\s*520px\)[\s\S]*\.caps\.detail-capacity-rail\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\)/.test(css), 'mobile volumes must stack vertically without a horizontal rail');
  assert(/@media\s*\(prefers-reduced-motion:\s*reduce\)[\s\S]*\.cap-fill\b[\s\S]*transition:\s*none\s*!important/.test(css), 'detail capacity fill animation must be explicitly disabled for reduced motion');
}
test('testDetailCapacityResponsiveCssContract',testDetailCapacityResponsiveCssContract);
function testDetailHeaderMobileCssContract() {
  const css = fs.readFileSync(path.join(here, 'styles.css'), 'utf8');
  assert(/@media\s*\(max-width:\s*520px\)[\s\S]*body\[data-shell-mode=['"]detail['"]\]\s+\.head-row\b[\s\S]*display:\s*grid[\s\S]*grid-template-columns:\s*minmax\(0,\s*1fr\)\s+auto/.test(css), '390px detail header must use a bounded two-column navigation row');
  assert(/@media\s*\(max-width:\s*520px\)[\s\S]*body\[data-shell-mode=['"]detail['"]\]\s+\.brand-shell\b[\s\S]*display:\s*none/.test(css), '390px detail header must omit the redundant product title');
}
test('testDetailHeaderMobileCssContract',testDetailHeaderMobileCssContract);
function testMountCentricResponsiveCssContract() {
  const css = fs.readFileSync(path.join(here, 'styles.css'), 'utf8');
  const mediaColumnMatch = css.match(/--overview-media-column-width:\s*([^;]+);/);
  assert(mediaColumnMatch, 'overview media column width token must exist');
  const mediaColumnWidthPx = Number.parseFloat(mediaColumnMatch[1]);
  assert.strictEqual(mediaColumnMatch[1].trim(), '56px', 'overview media column width token must be set to 56px to avoid text clipping across browsers');
  assert.strictEqual(mediaColumnWidthPx, 56, 'overview media column token must remain an integer CSS pixel width');
  const mediaLabelBlock = cssBlock(css, '.overview-media-label');
  const mediaLabelBorderMatch = mediaLabelBlock.match(/border:\s*([^;]+);/);
  assert(mediaLabelBorderMatch, 'overview media label must use an explicit border in its baseline state');
  const borderWidth = Number.parseFloat(mediaLabelBorderMatch[1].match(/([\d.]+)px/)[0]);
  assert(mediaColumnWidthPx - (2 * borderWidth) >= 54, 'media-column width is border-box; 56px minus borders should leave at least 54px for label text (content-area-safe, no clipping workaround)');
  assert(!/text-overflow\s*:\s*ellipsis/.test(mediaLabelBlock), 'media labels should not rely on text-overflow truncation; containment-only clipping is expected to avoid overlap');
  assert(/--overview-max-width:\s*1440px/.test(css), 'overview CSS must expose a semantic max-width token');
  assert(/--overview-gutter:\s*24px/.test(css), 'overview CSS must expose the desktop gutter token');
  assert(/--overview-card-gap:\s*14px/.test(css), 'overview CSS must expose the card gap token');
  assert(/--overview-card-min:\s*340px/.test(css), 'overview CSS must expose the minimum card width token');
  assert(/body\[data-shell-mode=['"]overview['"]\]\s+\.head-row\b[\s\S]*width:\s*min\(100%,\s*calc\(var\(--overview-max-width\) \+ \(2 \* var\(--overview-gutter\)\)\)\)[\s\S]*padding:\s*0\.35rem var\(--overview-gutter\)/.test(css), 'overview header shell width must include gutters so header content edges match overview cards');
  assert(/\.overview-view\b[\s\S]*max-width:\s*var\(--overview-max-width\)/.test(css), 'overview view must consume the semantic max-width token');
  assert(/\.overview-list\b[\s\S]*grid-template-columns:\s*repeat\(3,\s*minmax\(var\(--overview-card-min\),\s*1fr\)\)/.test(css), 'desktop overview must use three columns with the semantic minimum card width');
  assert(/\.overview-list\b[\s\S]*gap:\s*var\(--overview-card-gap\)/.test(css), 'desktop card gutter must use the semantic card gap token');
  assert(/@media \(max-width:\s*520px\)[\s\S]*\.overview-mount-metrics\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\)[^}]*\}[\s\S]*\.overview-mount-free\s*\{[^}]*min-width:\s*0[^}]*text-align:\s*left/.test(css), 'narrow mount rows must stack total/free capacity below the bar instead of causing horizontal overflow');
  assert(/--monitor-card-radius:\s*24px/.test(css), 'the shared monitor-card radius must use the GPU Monitor computed value, independent of root font size');
  assert(/--monitor-card-header-padding:\s*9px 12px 8px/.test(css), 'the shared monitor-card header padding must use the GPU Monitor computed value');
  assert(/--monitor-card-title-row-min-height:\s*30px/.test(css), 'the shared monitor-card title row must preserve the GPU Monitor edit-control height even when Storage has no edit button');
  assert(/--monitor-card-title-size:\s*15\.2px/.test(css), 'the shared monitor-card title size must use the GPU Monitor computed value');
  assert(/--monitor-card-title-line-height:\s*19\.76px/.test(css), 'the shared monitor-card title line height must use the GPU Monitor computed value');
  assert(/--monitor-card-status-dot-size:\s*8px/.test(css), 'the shared monitor-card status dot must use the GPU Monitor computed value');
  assert(/--monitor-card-meta-size:\s*9\.76px/.test(css), 'the shared monitor-card metadata size must use the GPU Monitor computed value');
  assert(/\.overview-card\b[\s\S]*border-radius:\s*var\(--monitor-card-radius\)/.test(css), 'server cards must consume the shared GPU Monitor card radius token');
  assert(/\.overview-card\b[\s\S]*background:\s*var\(--ops-card\)/.test(css), 'server cards must use the shared Clean card surface');
  assert(/--ops-card:\s*var\(--surface\)/.test(css) && /--ops-border:\s*var\(--separator\)/.test(css), 'Storage must expose the same semantic card aliases as GPU Monitor');
  assert(/\.overview-card\b[\s\S]*border:\s*1px solid var\(--ops-border\)/.test(css), 'Storage server cards must consume the shared GPU Monitor border token');
  assert(/\.overview-card\b[\s\S]*background:\s*var\(--ops-card\)/.test(css), 'Storage server cards must consume the shared GPU Monitor card token');
  assert(/html\.light\s+\.overview-card\b[\s\S]*box-shadow:\s*0 8px 22px color-mix\(in srgb, var\(--ops-fg\) 8%, transparent\)/.test(css), 'light-mode Storage overview cards must use a restrained Clean resting shadow');
  const overviewHeaderBlock = cssBlock(css, '.overview-card-header');
  assert(/padding:\s*var\(--monitor-card-header-padding\)/.test(overviewHeaderBlock), 'Storage card headers must consume the shared GPU Monitor header rhythm token');
  assert(/background:\s*transparent/.test(overviewHeaderBlock), 'Storage card headers must remain part of one continuous GPU Monitor card surface');
  assert(!/border-bottom\s*:/.test(overviewHeaderBlock), 'Storage card headers must not add a separate cap divider absent from GPU Monitor');
  assert(/#app\s*>\s*header\s*\{/.test(css), 'sticky frosted shell styling must be scoped to the direct app header');
  assert(!/(?:^|\n)\s*header\s*\{/.test(css), 'semantic headers inside monitor cards must not inherit the sticky shell border and backdrop');
  assert(/\.overview-card-title-row\b[\s\S]*display:\s*flex[\s\S]*align-items:\s*center/.test(css), 'Storage card headers must preserve the shared GPU Monitor title-row anatomy');
  assert(/\.overview-card-title-row\b[\s\S]*min-height:\s*var\(--monitor-card-title-row-min-height\)/.test(css), 'Storage card headers must preserve the shared GPU Monitor rendered height');
  assert(/\.overview-mounts\b[\s\S]*display:\s*flex[\s\S]*flex-direction:\s*column/.test(css), 'mounts must render as compact vertical monitor rows');
  assert(/\.overview-mount\b[\s\S]*grid-template-columns:\s*var\(--overview-media-column-width\)\s+minmax\(0,\s*1fr\)/.test(css), 'mount rows must allocate a dedicated media column and a flexible body column');
  assert(/\.overview-media-label\b[\s\S]*width:\s*var\(--overview-media-column-width\)/.test(css), 'overview media labels must consume the same dedicated media-column width as the grid track');
  assert(/\.overview-media-label\b[\s\S]*overflow:\s*hidden/.test(css), 'overview media labels must prevent overflow from intruding into mount path');
  assert(/\.overview-mount\[data-pressure="warning"\]\s+\.overview-mount-pct\s*\{[^}]*color:\s*var\(--warn\)/.test(css), 'warning color must be scoped to the exact warning percentage selector');
  assert(/\.overview-mount\[data-pressure="critical"\]\s+\.overview-mount-pct\s*\{[^}]*color:\s*var\(--crit\)/.test(css), 'critical color must be scoped to the exact critical percentage selector');
  assert(/\.overview-pressure-fill\[data-pressure="unknown"\][\s\S]*background:\s*var\(--text2\)/.test(css), 'unknown pressure bars must use a neutral color instead of inheriting OK green');
  assert(/@media\s*\(max-width:\s*1095px\)[\s\S]*\.overview-list\b[\s\S]*grid-template-columns:\s*repeat\(2,\s*minmax\(var\(--overview-card-min\),\s*1fr\)\)/.test(css), 'overview must switch to two columns before three cards would fall below 340px');
  assert(/@media\s*\(max-width:\s*733px\)[\s\S]*\.overview-list\b[\s\S]*grid-template-columns:\s*minmax\(0,\s*1fr\)/.test(css), 'overview must switch to one column before two cards would fall below 340px');
  assert(/\.overview-pressure-bar\b[\s\S]*height:\s*4px/.test(css), 'compact overview pressure bars must be 4px tall');
  assert(/\.overview-mount-path\b[\s\S]*text-overflow:\s*ellipsis/.test(css), 'compact mount paths must truncate visually');
  assert(/font-variant-numeric:\s*tabular-nums/.test(css), 'compact numeric fields must use tabular numbers');
  assert(/@media\s*\(max-width:\s*733px\)[\s\S]*--overview-gutter:\s*12px/.test(css), '390px mobile overview layouts must use the approved 12px semantic gutter');
  assert(/@media\s*\(max-width:\s*520px\)[\s\S]*\.overview-card\b[\s\S]*overflow:\s*hidden/.test(css), 'mobile overview cards must explicitly hide horizontal overflow');
  assert(/@media\s*\(prefers-reduced-motion:\s*reduce\)/.test(css), 'overview styling must continue to respect reduced motion');
}
test('testMountCentricResponsiveCssContract',testMountCentricResponsiveCssContract);
function testStorageMonitorBetaBadgeExplainsPreviewRisk() {
  const html = fs.readFileSync(path.join(here, 'index.html'), 'utf8');
  const css = fs.readFileSync(path.join(here, 'styles.css'), 'utf8');
  const titleStart = html.indexOf('<h1 class="brand-title">Storage Monitor</h1>');
  const shellEnd = html.indexOf('</div>', titleStart);
  const brandShell = html.slice(titleStart, shellEnd);
  assert(titleStart >= 0, 'Storage Monitor title must remain visible');
  assert.match(brandShell, /class="beta-badge"[^>]*>Beta<\/span>/, 'Beta badge must sit beside the Storage Monitor title');
  assert.match(brandShell, /(?:title|aria-label)="[^"]*버그가 있을 수 있습니다[^"]*"/, 'the badge must warn that the beta can contain bugs');
  assert.match(css, /\.beta-badge\s*\{[^}]*border-radius:\s*999px[^}]*font-size:\s*10px/);
}
test('testStorageMonitorBetaBadgeExplainsPreviewRisk',testStorageMonitorBetaBadgeExplainsPreviewRisk);
function selectorSpecificity(selector) {
  const ids = (selector.match(/#[\w-]+/g) || []).length;
  const classes = (selector.match(/\.[\w-]+|\[[^\]]+\]|:(?!:)[\w-]+(?:\([^)]*\))?/g) || []).length;
  const elements = (selector.replace(/#[\w-]+|\.[\w-]+|\[[^\]]+\]|:{1,2}[\w-]+(?:\([^)]*\))?/g, ' ').match(/\b[a-z][\w-]*\b/gi) || []).length;
  return [ids, classes, elements];
}
function compareSpecificity(a, b) {
  for (let i = 0; i < 3; i += 1) {
    if (a[i] !== b[i]) return a[i] - b[i];
  }
  return 0;
}
function cssRuleBody(css, selector) {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&').replace(/\s+/g, '\\s+');
  const match = css.match(new RegExp(escaped + '\\s*\\{([^}]*)\\}'));
  return match ? match[1] : '';
}
function testThemeIconTransformSelectorsWinCascade() {
  const css = fs.readFileSync(path.join(here, "styles.css"), "utf8");
  const baseSelector = ".theme-mode-button .theme-icon";
  const hiddenSelectors = [".theme-mode-button .theme-icon-sun", ".theme-mode-button .theme-icon-moon"];
  const visibleSelector = "html.dark .theme-mode-button .theme-icon-sun, html.light .theme-mode-button .theme-icon-moon";
  const baseSpecificity = selectorSpecificity(baseSelector);

  assert(/transform:\s*translate\(-50%,\s*-50%\)/.test(cssRuleBody(css, baseSelector)), "base icon rule should only establish center translation");
  for (const selector of hiddenSelectors) {
    const body = cssRuleBody(css, selector);
    assert(body, `${selector} hidden transform rule must exist`);
    assert(compareSpecificity(selectorSpecificity(selector), baseSpecificity) >= 0, `${selector} must be at least as specific as the base icon selector so hidden rotation/scale wins the cascade`);
    assert(/transform:\s*translate\(-50%,\s*-50%\)\s*rotate\([^)]*\)\s*scale\(\.72\)/.test(body), `${selector} must include the hidden translate + rotate + scale transform`);
  }
  for (const selector of visibleSelector.split(/,\s*/)) {
    assert(compareSpecificity(selectorSpecificity(selector), baseSpecificity) > 0, `${selector} visible state must be more specific than the base icon selector`);
  }
  assert(/html\.dark\s+\.theme-mode-button\s+\.theme-icon-sun\s*,\s*html\.light\s+\.theme-mode-button\s+\.theme-icon-moon\s*\{[^}]*opacity:\s*1[^}]*transform:\s*translate\(-50%,\s*-50%\)\s*rotate\(0deg\)\s*scale\(1\)/.test(css), "visible state selector must preserve centered translate and override hidden rotation/scale");
}
test('testThemeIconTransformSelectorsWinCascade',testThemeIconTransformSelectorsWinCascade);
function testOverviewMonitorCardCssContract() {
  const css = fs.readFileSync(path.join(here, "styles.css"), "utf8");
  assert(/--overview-max-width:\s*1440px/.test(css), "overview CSS must expose the 1440px max-width token");
  assert(/--overview-card-min:\s*340px/.test(css), "overview CSS must expose the 340px card minimum token");
  assert(/body\[data-shell-mode=['"]overview['"]\]\s+\.head-row\b[\s\S]*width:\s*min\(100%,\s*calc\(var\(--overview-max-width\) \+ \(2 \* var\(--overview-gutter\)\)\)\)[\s\S]*padding:\s*0\.35rem var\(--overview-gutter\)/.test(css), "overview header content edges must align with the overview card shell");
  assert(/\.overview-view\b[\s\S]*max-width:\s*var\(--overview-max-width\)[\s\S]*margin:\s*0 auto/.test(css), "overview content must be capped by the semantic max-width token and centered");
  assert(/\.overview-list\b[\s\S]*grid-template-columns:\s*repeat\(3,\s*minmax\(var\(--overview-card-min\),\s*1fr\)\)/.test(css), "overview must use an explicit three-column card rhythm with minimum card width");
  assert(/\.overview-card\b[\s\S]*border-radius:\s*(?:24px|1\.5rem)/.test(css), "overview cards must share GPU Monitor card geometry");
  assert(/\.overview-mounts\b[\s\S]*flex-direction:\s*column/.test(css), "overview mounts must stack as compact monitor rows");
  assert(/\.overview-pressure-fill\b[\s\S]*background:\s*var\(--accent\)/.test(css), "healthy capacity graphs must reuse the suite accent color");
  assert(/@media\s*\(max-width:\s*1095px\)[\s\S]*\.overview-list\b[\s\S]*grid-template-columns:\s*repeat\(2,\s*minmax\(var\(--overview-card-min\),\s*1fr\)\)/.test(css), "compact overview must use two explicit columns above the 340px minimum");
  assert(/@media\s*\(max-width:\s*733px\)[\s\S]*\.overview-list\b[\s\S]*grid-template-columns:\s*minmax\(0,\s*1fr\)/.test(css), "mobile overview must use one explicit column below the two-card minimum");
  assert(/body\[data-shell-mode=['"]detail['"]\]\s+main\b[\s\S]*max-width:\s*none/.test(css), "detail mode must remain full width");
  assert(/@media\s*\(max-width:\s*520px\)[\s\S]*\.overview-card\b[\s\S]*overflow:\s*hidden/.test(css), "mobile overview cards must explicitly guard against horizontal overflow");
}
test('testOverviewMonitorCardCssContract',testOverviewMonitorCardCssContract);
