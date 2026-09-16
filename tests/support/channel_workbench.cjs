'use strict';
// Load the shared channel presentation module into a vm sandbox the way
// chat.html does (change upgrade-personal-channel-workbench, task 3.1).
//
// channel-workbench.js is deferred after fragments.js and before console.js, so
// the contract tests that extract a single console.js function and evaluate it
// in isolation must evaluate this module first: console.js's presentation
// functions are thin delegations to `window.ChannelWorkbench` now, and a sandbox
// without the module throws on every one of them.
//
// The module also needs a `window` on the sandbox to attach to — console.js
// reads `window.ChannelWorkbench` (not a bare global), so `loadWithModule` adds
// an empty one when the caller has not already supplied its own (e.g. a stub
// `window.prompt`).
const fs = require('node:fs');
const path = require('node:path');

const SOURCE_PATH = path.join(
    __dirname, '../../channel/web/static/js/channel-workbench.js');

function moduleSource() {
    return fs.readFileSync(SOURCE_PATH, 'utf8');
}

// The source to hand to vm.runInNewContext: the shared module first, then the
// console.js fragments the test extracted. The sandbox is given a `window` if it
// has none, because that is where the module publishes its API.
function loadWithModule(sources, sandbox) {
    if (sandbox && !sandbox.window) sandbox.window = {};
    return [moduleSource()].concat(sources).join('\n');
}

module.exports = { SOURCE_PATH, moduleSource, loadWithModule };
