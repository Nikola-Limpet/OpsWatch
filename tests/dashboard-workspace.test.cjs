const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');

function app(data) {
  const elements = new Map();
  const documentListeners = new Map();
  const element = (id) => {
    if (!elements.has(id)) elements.set(id, {
      value: '', hidden: false, innerHTML: '', textContent: '', dataset: {},
      classList: { add() {}, remove() {}, toggle() {} },
      setAttribute() {}, removeAttribute() {}, addEventListener() {},
      focus() { context.document.activeElement = this; },
      querySelector() { return null; }, contains() { return false; },
      showModal() { this.open = true; },
      closest(selector) {
        const attribute = selector.match(/^\[data-([a-z-]+)\]$/)?.[1];
        if (!attribute) return null;
        const key = attribute.replace(/-([a-z])/g, (_, c) => c.toUpperCase());
        return Object.hasOwn(this.dataset, key) ? this : null;
      },
    });
    return elements.get(id);
  };
  const context = vm.createContext({
    document: {
      getElementById: element, querySelector: element, querySelectorAll() { return []; },
      addEventListener(type, listener) {
        if (!documentListeners.has(type)) documentListeners.set(type, []);
        documentListeners.get(type).push(listener);
      },
      body: { dataset: {} }, activeElement: null,
    },
    location: { hash: '#overview' }, window: { addEventListener() {} },
    fetch: () => new Promise(() => {}), setInterval() {}, setTimeout() {}, clearTimeout() {},
    AbortSignal, URL, console, fixture: data,
  });
  vm.runInContext(fs.readFileSync('src/opswatch/dashboard_assets/app.js', 'utf8'), context);
  vm.runInContext('state.data = fixture; state.refreshing = false;', context);
  return {
    run: (code) => vm.runInContext(code, context),
    json: (code) => JSON.parse(vm.runInContext(`JSON.stringify(${code})`, context)),
    element,
    click: (target) => documentListeners.get('click').forEach((listener) => listener({target})),
  };
}

const incident = (ref, target, kind = 'monitoring', error = 'Unauthorized') => ({
  ref, target, kind, status: 'open', severity: 'warning', title: 'Cannot collect data',
  evidence: { error }, updated: 100, repair: { available: false },
});
const fixture = () => ({
  services: [
    { id: 'api', name: 'API', provider: 'Railway', account: 'first' },
    { id: 'redis', name: 'Redis', provider: 'Railway', account: 'first' },
    { id: 'other', name: 'Other API', provider: 'Railway', account: 'second' },
  ],
  incidents: [], repairs: [], projects: [],
  summary: { total: 3, healthy: 2, attention: 1, unknown: 0, open_incidents: 1 },
  monitor: { status: 'running' }, repair_worker: { status: 'running' }, timezone: 'UTC',
});

test('collector grouping keeps different accounts and error causes separate', () => {
  const data = fixture();
  data.incidents = [
    incident('OW-0001', 'api'), incident('OW-0002', 'redis'),
    incident('OW-0003', 'other'), incident('OW-0004', 'api', 'monitoring', 'Network timeout'),
    incident('OW-0005', 'api', 'application error'),
  ];
  const view = app(data);
  const groups = view.json('groupIncidents(state.data.incidents)');
  assert.equal(groups.length, 4);
  assert.deepEqual(groups[0].items.map(i => i.ref), ['OW-0001', 'OW-0002']);
  assert.equal(groups[1].items[0].ref, 'OW-0003');
  assert.equal(groups[2].items[0].ref, 'OW-0004');
  assert.equal(groups[3].items[0].ref, 'OW-0005');
});

test('a missing account cannot merge collector failures from different services', () => {
  const data = fixture();
  delete data.services[0].account;
  delete data.services[1].account;
  data.incidents = [incident('OW-0001', 'api'), incident('OW-0002', 'redis')];
  assert.equal(app(data).json('groupIncidents(state.data.incidents)').length, 2);
});

test('incident search finds every ref inside a collector group', () => {
  const data = fixture();
  data.incidents = [incident('OW-0001', 'api'), incident('OW-0002', 'redis')];
  const view = app(data);
  view.element('incident-search').value = 'OW-0002';
  view.element('incident-status').value = 'open';
  assert.deepEqual(view.json('filteredIncidents()').map(i => i.ref), ['OW-0002']);
});

test('active filter includes a running repair and excludes unrelated open incidents', () => {
  const data = fixture();
  data.incidents = [incident('OW-0001', 'api'), incident('OW-0002', 'redis')];
  data.repairs = [{ id: 'RF-0001', incident: 'OW-0002', status: 'validating' }];
  const view = app(data);
  view.element('incident-status').value = 'active';
  assert.deepEqual(view.json('filteredIncidents()').map(i => i.ref), ['OW-0002']);
});

test('stale or disconnected snapshots cannot claim healthy coverage or worker readiness', () => {
  const data = fixture();
  data.monitor.status = 'stale';
  const view = app(data);
  assert.equal(view.json('workspaceHealth()').healthy, 0);
  assert.equal(view.json('workspaceHealth()').unknown, 3);
  assert.equal(view.json('workspaceHealth()').worker, 'unverified');
  data.monitor.status = 'running';
  const disconnected = app(data);
  disconnected.run('state.failed = true');
  assert.equal(disconnected.json('workspaceHealth()').healthy, 0);
});

test('repair actions are unavailable on stale readings and existing PRs', () => {
  const data = fixture();
  const view = app(data);
  view.run('candidate = { ref: "OW-0001", evidence: {message: "TypeError at order.ts:12"}, repair: { available: true } }');
  assert.equal(view.run('canStartRepair(candidate, null)'), true);
  assert.equal(view.run('canStartRepair(candidate, {status: "pr_open"})'), false);
  view.run('state.failed = true');
  assert.equal(view.run('canStartRepair(candidate, null)'), false);
});

test('interrupted jobs require attention rather than appearing to run indefinitely', () => {
  const view = app(fixture());
  assert.deepEqual(view.json('jobPresentation({status: "interrupted"})'), {
    label: 'Needs manual review', tone: 'warning', phase: -1, active: false,
  });
});

function serviceFixture() {
  const data = fixture();
  const service = (id, provider, enabled, status) => ({
    id, name: id, provider, enabled, status, environment: 'production',
    latency_ms: null, threshold_ms: 1500, checked: null, checks: [],
  });
  const api = service('Store API', 'Railway', true, 'warning');
  const db = service('Store database', 'Neon', false, 'disabled');
  const web = service('Store web', 'Vercel', true, 'healthy');
  const demo = service('Demo API', 'Railway', false, 'disabled');
  data.services = [api, web];
  data.projects = [
    { id: 'store', name: 'Store', enabled_count: 2, services: [api, db, web] },
    { id: 'demo', name: 'Demo', enabled_count: 0, services: [demo] },
  ];
  data.selection_editable = true;
  return data;
}

test('Monitored hides disabled services while keeping enabled unhealthy services', () => {
  const view = app(serviceFixture());
  view.run('renderServices()');
  assert.match(view.element('services-table').innerHTML, /Store API/);
  assert.doesNotMatch(view.element('services-table').innerHTML, /Store database|Demo API/);
  assert.equal(view.element('tab-monitored-count').textContent, '2');
  assert.equal(view.element('tab-inactive-count').textContent, '2');
});

test('Inactive includes paused services from mixed and entirely inactive projects', () => {
  const view = app(serviceFixture());
  view.run('state.serviceTab = "inactive"; renderServices()');
  assert.match(view.element('services-table').innerHTML, /Store database/);
  assert.match(view.element('services-table').innerHTML, /Demo API/);
  assert.doesNotMatch(view.element('services-table').innerHTML, /Store API|Store web/);
});

test('project-name search and provider and health filters compose within a tab', () => {
  const view = app(serviceFixture());
  view.element('service-search').value = 'Store';
  view.element('provider-filter').value = 'Railway';
  view.element('status-filter').value = 'attention';
  view.run('renderServices()');
  assert.match(view.element('services-table').innerHTML, /Store API/);
  assert.doesNotMatch(view.element('services-table').innerHTML, /Store web|Store database|Demo API/);
  assert.match(view.element('services-table').innerHTML, /Pause all 3 services in Store/);
});

test('inactive list ignores the monitored health filter rather than hiding paused services', () => {
  const view = app(serviceFixture());
  view.element('status-filter').value = 'attention';
  view.run('state.serviceTab = "inactive"; renderServices()');
  assert.match(view.element('services-table').innerHTML, /Store database/);
  assert.match(view.element('services-table').innerHTML, /Demo API/);
});

test('collapsing a project hides its rows without changing the monitored total', () => {
  const view = app(serviceFixture());
  view.run('state.collapsedProjects.add("store"); renderServices()');
  assert.doesNotMatch(view.element('services-table').innerHTML, /data-service-settings=/);
  assert.equal(view.element('tab-monitored-count').textContent, '2');
  view.run('renderServices()');
  assert.doesNotMatch(view.element('services-table').innerHTML, /data-service-settings=/);
});

test('paging bounds the project list but search still finds later projects', () => {
  const data = serviceFixture();
  data.projects = Array.from({length: 12}, (_, i) => ({
    id: `p${i}`, name: `Project ${String(i).padStart(2, '0')}`, enabled_count: 1,
    services: [{...data.services[1], id: `s${i}`, name: `Service ${i}`}],
  }));
  const view = app(data);
  view.run('renderServices()');
  assert.match(view.element('services-table').innerHTML, /Service 0/);
  assert.doesNotMatch(view.element('services-table').innerHTML, /Service 10/);
  view.run('state.servicePage = 1; renderServices()');
  assert.match(view.element('services-table').innerHTML, /Service 10/);
  view.element('service-search').value = 'Project 11';
  view.run('renderServices()');
  assert.match(view.element('services-table').innerHTML, /Service 11/);
});

test('paused services remain configurable in the details panel after leaving the active list', () => {
  const view = app(serviceFixture());
  view.run('state.selectedService = "Store database"; renderServiceSettings()');
  assert.match(view.element('service-settings').innerHTML, /Store database/);
  assert.match(view.element('service-settings').innerHTML, /aria-checked="false"/);
  assert.match(view.element('service-settings').innerHTML, /data-monitor-enabled="true"/);
  assert.doesNotMatch(view.element('service-settings').innerHTML, /No issues detected/);
});

test('read-only and disconnected snapshots disable monitoring changes', () => {
  const data = serviceFixture();
  data.selection_editable = false;
  const view = app(data);
  assert.match(view.run('monitoringSwitch(fixture.projects[0].services[0])'), /disabled/);
  view.run('state.data.selection_editable = true; state.failed = true');
  assert.match(view.run('monitoringSwitch(fixture.projects[0].services[0])'), /disabled/);
});

test('clicking the service settings panel never triggers the delegated evidence action', () => {
  const view = app(serviceFixture());
  view.run('state.selectedService = "Store API"; renderServiceSettings()');
  view.click(view.element('service-settings'));
  assert.equal(view.run('state.dialog'), null);
});

test('explicit tab navigation exits service details so the destination list is visible on mobile', () => {
  const view = app(serviceFixture());
  view.run('state.selectedService = "Store API"; selectServiceTab("inactive")');
  assert.equal(view.run('state.selectedService'), null);
  assert.equal(view.element('service-settings').hidden, true);
  assert.match(view.element('services-table').innerHTML, /Store database/);
});

test('a delayed monitoring save does not steal focus from a search started while it was pending', async () => {
  const view = app(serviceFixture());
  const completed = view.run(`
    state.view = 'services';
    state.refreshPromise = null;
    requestAction = () => new Promise((resolve) => { completeSave = () => resolve({targets: ['Store API']}); });
    refresh = async () => {};
    changeMonitoring({dataset: {monitorScope: 'services', monitorId: 'Store API', monitorEnabled: 'false'}})
  `);
  view.run('document.activeElement = $("service-search"); completeSave()');
  await completed;
  assert.equal(view.run('document.activeElement === $("service-search")'), true);
});

test('legacy quiet-only evidence cannot offer an unexplained code repair', () => {
  const view = app(fixture());
  assert.equal(view.run('canStartRepair({repair: {available: true}, evidence: {quiet_seconds: 900}}, null)'), false);
});

test('missing legacy evidence does not offer a code repair', () => {
  const view = app(fixture());
  assert.equal(view.run('canStartRepair({repair: {available: true}, evidence: {}}, null)'), false);
});
