// Optional maintainer preview using the workflow's pinned release-please engine.
// Normal Python-only PR verification does not install Node dependencies.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
for (const protocol of ['node:http', 'node:https']) {
  require(protocol).request = require(protocol).get = () => { throw new Error('Unexpected network access'); };
}
const base = process.env.GITSTORE_RELEASE_PLEASE_MODULE;
assert(path.isAbsolute(base));
assert.equal(require(path.join(base, 'package.json')).version, '17.6.0');
require(base);
const load = name => require(path.join(base, 'build/src', name));
const {Manifest} = load('manifest');
const {buildStrategy} = load('factory');
const {Version} = load('version');
const {TagName} = load('util/tag-name');
const {parseConventionalCommits} = load('commit');
const root = path.resolve(__dirname, '..');
const read = name => fs.readFileSync(path.join(root, name), 'utf8');
const logger = {info(){}, debug(){}, warn(){}, error(){}};
const github = {repository:{owner:'gitstore-dev',repo:'data'},
  getFileJson: async name => JSON.parse(read(name))};
(async () => {
  const manifest = await Manifest.fromManifest(github, 'main', undefined, undefined, {logger});
  const config = manifest.repositoryConfig['.'];
  assert.equal(config.draft, true);
  assert.equal(config.forceTag, true);
  assert.equal(config.initialVersion, '0.1.0');
  const strategy = await buildStrategy({...config, github, path:'.', targetBranch:'main', logger});
  const history = parseConventionalCommits([{sha:'a'.repeat(40),
    message:'feat: extend the reviewed dataset', files:['datasets/definitions.json']}], logger);
  let contents = {};
  async function proposal(previous) {
    const result = await strategy.buildReleasePullRequest(history, previous ?
      {tag:new TagName(Version.parse(previous)),sha:'b'.repeat(40),notes:'previous'} : undefined);
    assert(result);
    const version = result.version.toString();
    for (const update of result.updates) {
      assert(['version.txt','CHANGELOG.md','datasets/definitions.json','datasets/selections.lock.json'].includes(update.path));
      const old = contents[update.path] ?? (fs.existsSync(path.join(root, update.path)) ? read(update.path) : '');
      contents[update.path] = await update.updater.updateContent(old, logger);
      if (update.path.endsWith('.json')) {
        const before = JSON.parse(old), after = JSON.parse(contents[update.path]);
        assert.equal(after.dataset_version, version);
        after.dataset_version = before.dataset_version;
        assert.deepEqual(after, before); // All category membership and pinned inputs preserved.
      }
    }
    assert.equal(contents['version.txt'].trim(), version);
    return version;
  }
  const first = await proposal(undefined); // No published release; manifest is 0.0.0.
  const second = await proposal(first);
  assert.equal(first, '0.1.0');
  assert.equal(second, '0.2.0');
  console.log(JSON.stringify({engine:'17.6.0',first,second,draft:config.draft,forceTag:config.forceTag,network:false,publication:false}));
})().catch(error => { console.error(error); process.exitCode = 1; });
