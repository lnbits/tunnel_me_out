const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const test = require('node:test')
const vm = require('node:vm')

function app(responses) {
  let definition
  const timers = []
  const context = {
    window: {},
    windowMixin: {},
    Vue: {createApp: value => (definition = value)},
    LNbits: {
      api: {request: async () => ({data: responses.shift()})},
      utils: {
        notifyApiError: error => {
          throw error
        }
      }
    },
    console,
    setTimeout: callback => {
      timers.push(callback)
      return timers.length
    },
    clearTimeout: () => {}
  }
  vm.runInNewContext(
    fs.readFileSync(path.join(__dirname, '../static/js/index.js'), 'utf8'),
    context
  )
  const instance = {...definition.data(), $q: {notify: () => {}}}
  for (const [name, method] of Object.entries(definition.methods)) {
    instance[name] = method.bind(instance)
  }
  return {instance, definition, timers}
}

test('refresh restores the pending invoice without a websocket', async () => {
  const {instance} = app([
    {
      tunnel: {
        status: 'pending',
        payment_hash: 'hash',
        payment_request: 'invoice'
      }
    }
  ])
  await instance.loadTunnel(true)
  assert.equal(instance.invoiceDialog.show, true)
  assert.equal(instance.invoiceDialog.payment_request, 'invoice')
})

test('payment completion uses actual reachability rather than claiming success', async () => {
  const {instance} = app([{tunnel: {status: 'active'}}, {reachable: false}])
  instance.invoiceDialog.show = true
  await instance.loadTunnel()
  assert.equal(instance.invoiceDialog.show, false)
  assert.equal(instance.reachable, false)
})

test('reconnect does not claim an unreachable tunnel is online', async () => {
  const {instance} = app([
    {status: 'active', last_error: 'Permission denied'},
    {reachable: false}
  ])
  await instance.reconnectTunnel()
  assert.equal(instance.reachable, false)
  assert.equal(instance.tunnel.last_error, 'Permission denied')
})

test('a poll finishing after unmount does not start another timer', async () => {
  const {instance, definition, timers} = app([{tunnel: null}])
  instance.schedulePoll()
  definition.beforeUnmount.call(instance)
  await timers[0]()
  assert.equal(timers.length, 1)
})

test('polling updates status without overwriting unsaved local binding edits', async () => {
  const saved = {status: 'pending', local_host: 'localhost', local_port: 5000}
  const {instance} = app([{tunnel: saved}, {tunnel: saved}])
  await instance.loadTunnel(true)
  assert.equal(instance.localHost, 'localhost')
  assert.equal(instance.localPort, 5000)
  instance.localHost = '192.168.1.10'
  instance.localPort = 9001
  await instance.loadTunnel()
  assert.equal(instance.localHost, '192.168.1.10')
  assert.equal(instance.localPort, 9001)
})
