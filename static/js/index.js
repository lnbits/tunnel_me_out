window.app = Vue.createApp({
  el: '#vue',
  mixins: [windowMixin],
  delimiters: ['${', '}'],
  data: function () {
    return {
      days: 1,
      wsBase: 'wss://lnbits.lnpro.xyz/api/v1/ws',
      loading: false,
      tunnel: null,
      reachable: null,
      invoiceDialog: {
        show: false,
        payment_request: '',
        payment_hash: ''
      },
      ws: null
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
    async loadTunnel() {
      try {
        const {data} = await LNbits.api.request('GET', '/tunnel_me_out/api/v1/tunnel', null)
        this.tunnel = data.tunnel
        if (this.tunnel && this.tunnel.status === 'pending') {
          this.invoiceDialog.payment_request = this.tunnel.payment_request
          this.invoiceDialog.payment_hash = this.tunnel.payment_hash
          this.invoiceDialog.show = true
          this.openWs()
        } else if (this.tunnel && this.tunnel.status === 'active') {
          this.checkReachability()
        }
      } catch (err) {
        console.error(err)
      }
    },
    async requestTunnel() {
      this.loading = true
      try {
        const body = {days: this.days}
        const {data} = await LNbits.api.request('POST', '/tunnel_me_out/api/v1/tunnel', null, body)
        this.tunnel = data
        this.invoiceDialog.payment_request = data.payment_request
        this.invoiceDialog.payment_hash = data.payment_hash
        this.invoiceDialog.show = true
        this.openWs()
        this.reachable = null
      } catch (err) {
        LNbits.utils.notifyApiError(err)
      } finally {
        this.loading = false
      }
    },
    async handlePaid() {
      await this.loadTunnel()
      this.invoiceDialog.show = false
      this.invoiceDialog.payment_hash = ''
      this.invoiceDialog.payment_request = ''
      if (this.ws) {
        this.ws.close()
      }
      this.reachable = true
    },
    openWs() {
      if (this.ws) {
        this.ws.close()
      }
      const hash = this.invoiceDialog.payment_hash
      if (!hash) return
      const url = `${this.wsBase}/${hash}`
      this.ws = new WebSocket(url)
      this.ws.onmessage = async (msg) => {
        try {
          const payload = JSON.parse(msg.data)
          if (payload && (payload.paid || payload.status === 'success')) {
            await this.handlePaid()
          }
        } catch (err) {
          console.error(err)
        }
      }
      this.ws.onclose = () => { this.ws = null }
    },
    async checkReachability() {
      this.reachable = null
      try {
        const {data} = await LNbits.api.request('GET', '/tunnel_me_out/api/v1/tunnel/ping', null)
        this.reachable = data.reachable
      } catch (err) {
        console.error(err)
        this.reachable = false
      }
    },
    async reconnectTunnel() {
      this.loading = true
      try {
        const {data} = await LNbits.api.request('POST', '/tunnel_me_out/api/v1/tunnel/reconnect', null)
        this.tunnel = data
        this.$q.notify({type: 'positive', message: 'Tunnel reconnecting'})
        this.reachable = true
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
    this.loadTunnel()
  },
  beforeUnmount() {
    if (this.ws) {
      this.ws.close()
    }
  }
})
