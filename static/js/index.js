window.app = Vue.createApp({
  el: '#vue',
  mixins: [windowMixin],
  delimiters: ['${', '}'],
  data: function () {
    return {
      days: 1,
      loading: false,
      tunnel: null,
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
    }
  },
  methods: {
    async loadTunnel() {
      try {
        const {data} = await LNbits.api.request('GET', '/tunnel_me_out/api/v1/tunnel', null)
        this.tunnel = data.tunnel
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
      } catch (err) {
        LNbits.utils.notifyApiError(err)
      } finally {
        this.loading = false
      }
    },
    openWs() {
      if (this.ws) {
        this.ws.close()
      }
      const hash = this.invoiceDialog.payment_hash
      if (!hash) return
      const url = `wss://satsy.co/api/v1/ws/${hash}`
      this.ws = new WebSocket(url)
      this.ws.onmessage = async (msg) => {
        try {
          const payload = JSON.parse(msg.data)
          if (payload && payload.paid) {
            await this.confirmTunnel()
            this.invoiceDialog.show = false
            this.ws.close()
          }
        } catch (err) {
          console.error(err)
        }
      }
      this.ws.onclose = () => { this.ws = null }
    },
    async confirmTunnel() {
      try {
        const params = new URLSearchParams({payment_hash: this.invoiceDialog.payment_hash})
        const {data} = await LNbits.api.request('POST', '/tunnel_me_out/api/v1/tunnel/confirm?' + params.toString(), null)
        this.tunnel = data
      } catch (err) {
        console.error(err)
      }
    },
    copyInvoice() {
      if (!this.invoiceDialog.payment_request) return
      LNbits.utils.copyText(this.invoiceDialog.payment_request)
      this.$q.notify({type: 'positive', message: 'Payment request copied'})
    }
  },
  mounted() {
    this.loadTunnel()
  }
})
