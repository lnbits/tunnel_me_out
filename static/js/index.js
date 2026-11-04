window.app = Vue.createApp({
  el: '#vue',
  mixins: [windowMixin],
  delimiters: ['${', '}'],
  data: function () {
    return {
      days: 1,
      localHost: '',
      localPort: null,
      loading: false,
      tunnel: null,
      reachable: null,
      invoiceDialog: {
        show: false,
        payment_request: '',
        payment_hash: ''
      },
      pollTimer: null,
      disposed: false
    }
  },
  computed: {
    progress() {
      if (!this.tunnel || !this.tunnel.expires_at) return 0
      const now = new Date()
      const exp = new Date(this.tunnel.expires_at)
      const totalMs = this.tunnel.days * 24 * 60 * 60 * 1000
      const remaining = Math.max(0, exp - now)
      return Math.min(1, remaining / totalMs)
    },
    progressLabel() {
      if (!this.tunnel || !this.tunnel.expires_at) return 'No tunnel yet'
      const exp = new Date(this.tunnel.expires_at)
      return `Expires at ${exp.toLocaleString()}`
    },
    expiresLabel() {
      if (!this.tunnel || !this.tunnel.expires_at) return 'Not set'
      return new Date(this.tunnel.expires_at).toLocaleString()
    }
  },
  methods: {
    async loadTunnel(restoreInvoice = false) {
      try {
        const {data} = await LNbits.api.request(
          'GET',
          '/tunnel_me_out/api/v1/tunnel',
          null
        )
        this.tunnel = data.tunnel
        // Restore saved inputs on page load; polling must preserve edits.
        if (this.tunnel && restoreInvoice) {
          this.localHost = this.tunnel.local_host || this.localHost
          this.localPort = this.tunnel.local_port || this.localPort
        }
        if (this.tunnel && this.tunnel.status === 'pending') {
          this.invoiceDialog.payment_request = this.tunnel.payment_request
          this.invoiceDialog.payment_hash = this.tunnel.payment_hash
          if (restoreInvoice) this.invoiceDialog.show = true
        } else if (this.tunnel) {
          this.invoiceDialog.show = false
          await this.checkReachability()
        }
      } catch (err) {
        console.error(err)
      }
    },
    async requestTunnel() {
      this.loading = true
      try {
        const body = {
          days: this.days,
          local_host: this.localHost || undefined,
          local_port: this.localPort || undefined
        }
        const {data} = await LNbits.api.request(
          'POST',
          '/tunnel_me_out/api/v1/tunnel',
          null,
          body
        )
        this.tunnel = data
        this.localHost = this.tunnel.local_host || this.localHost
        this.localPort = this.tunnel.local_port || this.localPort
        this.invoiceDialog.payment_request = data.payment_request
        this.invoiceDialog.payment_hash = data.payment_hash
        this.invoiceDialog.show = data.status === 'pending'
        this.schedulePoll()
        this.reachable = null
      } catch (err) {
        LNbits.utils.notifyApiError(err)
      } finally {
        this.loading = false
      }
    },
    schedulePoll() {
      if (this.disposed) return
      clearTimeout(this.pollTimer)
      this.pollTimer = setTimeout(
        async () => {
          await this.loadTunnel()
          this.schedulePoll()
        },
        this.tunnel?.status === 'pending' ? 5000 : 30000
      )
    },
    async checkReachability() {
      this.reachable = null
      try {
        const {data} = await LNbits.api.request(
          'GET',
          '/tunnel_me_out/api/v1/tunnel/ping',
          null
        )
        this.reachable = data.reachable
      } catch (err) {
        console.error(err)
        this.reachable = false
      }
    },
    async reconnectTunnel() {
      this.loading = true
      try {
        const {data} = await LNbits.api.request(
          'POST',
          '/tunnel_me_out/api/v1/tunnel/reconnect',
          null
        )
        this.tunnel = data
        this.$q.notify({type: 'positive', message: 'Tunnel reconnecting'})
        await this.checkReachability()
      } catch (err) {
        this.reachable = false
        LNbits.utils.notifyApiError(err)
      } finally {
        this.loading = false
      }
    },
    copyInvoice() {
      if (!this.invoiceDialog.payment_request) return
      LNbits.utils.copyText(this.invoiceDialog.payment_request)
      this.$q.notify({type: 'positive', message: 'Payment request copied'})
    },
    copyScript() {
      if (!this.tunnel || !this.tunnel.ssh_command) return
      LNbits.utils.copyText(this.tunnel.ssh_command)
      this.$q.notify({type: 'positive', message: 'Command copied to clipboard'})
    }
  },
  mounted() {
    this.loadTunnel(true)
    this.schedulePoll()
  },
  beforeUnmount() {
    this.disposed = true
    clearTimeout(this.pollTimer)
  }
})
