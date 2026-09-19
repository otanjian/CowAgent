// Search-provider credentials in the fork console (change
// adopt-upstream-web-split follow-up).
//
// The runtime supports nine search providers and the fork's ModelsHandler
// advertises all nine (`_SEARCH_PROVIDERS`), but the console that is actually
// served (`static/js/console.js` + the `i18n/` namespaces it loads) only knew
// the original six: tavily/keenable would open the *model-vendor* modal and
// searxng had no instance-URL field at all, so three providers the backend
// reports as configurable could not be configured. This pins the contract the
// backend already implements:
//
//   1. the loaded i18n dictionaries carry the three new providers' copy in
//      every language (upstream's copy, so the Phase 3 adjudication is a no-op);
//   2. routing: a dedicated-credential provider opens the credential modal,
//      a vendor-backed one opens the vendor modal;
//   3. payloads: searxng posts `url`, anysearch/keenable post their key and
//      `anonymous` (empty key = the keyless tier), the rest post `api_key`;
//   4. searxng's field is prefilled from `url_masked`, is not a masked
//      sentinel, and is labelled as an instance URL rather than an API key.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const { loadDictionaries } = require('./support/i18n_namespaces.cjs');

const ROOT = path.join(__dirname, '..');
const consoleJs = fs.readFileSync(
    path.join(ROOT, 'channel/web/static/js/console.js'), 'utf8');

const LANGS = ['zh', 'zh-Hant', 'en'];
const NEW_PROVIDERS = ['tavily', 'searxng', 'keenable'];
const KEY_PROVIDERS = ['bocha', 'anysearch', 'serply', 'tavily', 'keenable', 'searxng'];
const VENDOR_PROVIDERS = ['zhipu', 'qianfan', 'linkai'];

function extractFunction(src, name) {
    const head = `function ${name}(`;
    const from = src.indexOf(head);
    assert.ok(from >= 0, `Missing ${name} in console.js`);
    let depth = 0;
    for (let i = src.indexOf('{', from); i < src.length; i++) {
        if (src[i] === '{') depth++;
        else if (src[i] === '}') {
            depth--;
            if (depth === 0) return src.slice(from, i + 1);
        }
    }
    throw new Error(`Unbalanced ${name}`);
}

// -- 1. the copy exists in every language the namespace declares ------------

test('every loaded dictionary has the three new providers title and desc', () => {
    const dict = loadDictionaries();
    for (const lang of LANGS) {
        assert.ok(dict[lang], `${lang} dictionary is loaded`);
        for (const pid of NEW_PROVIDERS) {
            for (const suffix of ['_title', '_desc']) {
                const key = `models_search_${pid}${suffix}`;
                const value = dict[lang][key];
                assert.equal(typeof value, 'string', `${lang}/${key} is a string`);
                assert.ok(value.trim().length > 0, `${lang}/${key} is non-empty`);
            }
        }
    }
});

// -- a sandbox that runs the credential functions the way the page does -----

function makeSandbox({ fetchCalls, openVendorCalls, openKeyCalls, input }) {
    const modal = { id: '', className: '', innerHTML: '', addEventListener() {}, remove() {} };
    const document = {
        body: { appendChild() {} },
        getElementById: id => (id === 'search-key-input' ? input : null),
        createElement: () => modal,
        addEventListener() {},
        removeEventListener() {},
    };
    const sandbox = {
        document,
        console,
        modelsState: {
            capabilities: {
                search: {
                    providers: [
                        { id: 'searxng', url_masked: 'https://sx.example.com', configured: true },
                        { id: 'tavily', api_key_masked: 'sk-masked', configured: true },
                    ],
                },
            },
        },
        t: key => key,
        escapeHtml: value => String(value == null ? '' : value),
        loadModelsView() {},
        showStatus() {},
        openVendorModal(pid) { openVendorCalls.push(pid); },
        openSearchKeyModal(pid, meta) { openKeyCalls.push([pid, meta]); },
        fetch: (url, options) => {
            fetchCalls.push(JSON.parse((options || {}).body || '{}'));
            return Promise.resolve({ json: () => Promise.resolve({ status: 'success' }) });
        },
        Promise,
        JSON,
    };
    vm.createContext(sandbox);
    const providerList = consoleJs.match(/const DEDICATED_SEARCH_CREDENTIALS = \[[^\]]*\];/);
    assert.ok(providerList, 'console.js declares the dedicated-credential provider list');
    vm.runInContext(providerList[0], sandbox);
    for (const name of ['_launchSearchProviderConfig', 'openSearchKeyModal',
        '_saveSearchKey', '_clearSearchKey', '_postSearchCredential']) {
        if (consoleJs.includes(`function ${name}(`)) {
            vm.runInContext(extractFunction(consoleJs, name), sandbox);
        }
    }
    return { sandbox, modal };
}

function stubInput(value = '', masked = false) {
    return {
        value,
        dataset: masked ? { masked: '1' } : {},
        focus() {},
        addEventListener() {},
        classList: { remove() {}, add() {} },
    };
}

// -- 2. routing -------------------------------------------------------------

test('a dedicated-credential provider opens the credential modal, not the vendor one', () => {
    const openVendorCalls = [];
    const openKeyCalls = [];
    const { sandbox } = makeSandbox({
        fetchCalls: [], openVendorCalls, openKeyCalls, input: stubInput(),
    });
    // Record the credential-modal hand-off instead of actually rendering it.
    sandbox.openSearchKeyModal = pid => openKeyCalls.push(pid);
    for (const pid of KEY_PROVIDERS) sandbox._launchSearchProviderConfig(pid, {});
    assert.deepEqual(openVendorCalls, [], 'no search provider is a model vendor');
    assert.deepEqual(openKeyCalls, KEY_PROVIDERS);

    const vendorCalls = [];
    const { sandbox: vendorSandbox } = makeSandbox({
        fetchCalls: [], openVendorCalls: vendorCalls, openKeyCalls: [], input: stubInput(),
    });
    for (const pid of VENDOR_PROVIDERS) vendorSandbox._launchSearchProviderConfig(pid, {});
    assert.deepEqual(vendorCalls, VENDOR_PROVIDERS,
        'providers reusing a model-vendor credential still go to the vendor modal');
});

// -- 3. payloads ------------------------------------------------------------

function savePayloads(pid, value, masked = false) {
    const fetchCalls = [];
    const { sandbox } = makeSandbox({
        fetchCalls, openVendorCalls: [], openKeyCalls: [], input: stubInput(value, masked),
    });
    sandbox._saveSearchKey(pid);
    return fetchCalls;
}

function clearPayloads(pid) {
    const fetchCalls = [];
    const { sandbox } = makeSandbox({
        fetchCalls, openVendorCalls: [], openKeyCalls: [], input: stubInput(),
    });
    sandbox._clearSearchKey(pid);
    return fetchCalls;
}

test('searxng posts and clears an instance url, never an api key', () => {
    const saved = savePayloads('searxng', 'https://sx.example.com');
    assert.equal(saved.length, 1);
    assert.equal(saved[0].provider, 'searxng');
    assert.equal(saved[0].url, 'https://sx.example.com');
    assert.ok(!('api_key' in saved[0]), 'searxng has no key field');

    const cleared = clearPayloads('searxng');
    assert.equal(cleared.length, 1);
    assert.equal(cleared[0].url, '', 'the clear button empties the stored URL');
    assert.ok(!('api_key' in cleared[0]));
});

test('an empty key on a keyless-capable provider enables its anonymous tier', () => {
    for (const pid of ['anysearch', 'keenable']) {
        const calls = savePayloads(pid, '');
        assert.equal(calls.length, 1, `${pid}: an empty key is still a write`);
        assert.equal(calls[0].provider, pid);
        assert.equal(calls[0].api_key, '');
        assert.equal(calls[0].anonymous, true, `${pid}: an empty key means anonymous`);
    }
});

test('a typed key wins over the anonymous tier, and plain providers just send it', () => {
    for (const pid of ['anysearch', 'keenable']) {
        const calls = savePayloads(pid, 'sk-live');
        assert.equal(calls[0].api_key, 'sk-live');
        assert.equal(calls[0].anonymous, false, `${pid}: a key turns anonymous off`);
    }
    for (const pid of ['bocha', 'serply', 'tavily']) {
        const calls = savePayloads(pid, 'sk-live');
        assert.equal(calls[0].api_key, 'sk-live');
        assert.ok(!('anonymous' in calls[0]), `${pid} has no anonymous tier`);
    }
});

test('the masked sentinel is kept, not re-submitted as a key', () => {
    assert.deepEqual(savePayloads('tavily', 'sk-masked', true), [],
        'leaving the masked value untouched writes nothing');
});

// -- 4. the searxng field is a URL, not a secret ----------------------------

test('searxng is prefilled with its url and labelled as an instance url', () => {
    const { sandbox, modal } = makeSandbox({
        fetchCalls: [], openVendorCalls: [], openKeyCalls: [], input: stubInput(),
    });
    sandbox.openSearchKeyModal('searxng', { url_masked: 'https://sx.example.com' });
    assert.match(modal.innerHTML, /Instance URL/, 'the field is labelled as a URL');
    assert.match(modal.innerHTML, /https:\/\/sx\.example\.com/, 'the stored URL prefills it');
    assert.ok(!/data-masked="1"/.test(modal.innerHTML),
        'a URL is not a masked sentinel: it stays editable');

    sandbox.openSearchKeyModal('tavily', { api_key_masked: 'sk-masked' });
    assert.match(modal.innerHTML, /models_search_tavily_title/,
        'the key providers use the per-provider title key');
    assert.match(modal.innerHTML, /API Key/);
});
