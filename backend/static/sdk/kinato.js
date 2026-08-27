/**
 * Kinato JavaScript SDK (v2.0.0)
 * Real-time checkout/cart tracking for AI-powered revenue recovery.
 *
 * Usage:
 *   <script src="https://your-kinato-domain.com/sdk/kinato.js"></script>
 *   <script>
 *     Kinato.init({ apiKey: "pk_test_..." });
 *     Kinato.identify({
 *       externalId: "cust_99", name: "Priya", phone: "+91...", email: "priya@example.com",
 *       consent: { voice: true, email: true }
 *     });
 *     Kinato.track("checkout.started", {
 *       checkoutId: "chk_local_123", amount: 3499, currency: "INR", productIds: ["sku_1"]
 *     });
 *   </script>
 *
 * NOTE: this SDK does not decide when a checkout is "abandoned" - that is
 * determined server-side by a durable sweeper (never a client-side timer,
 * which a closed tab or a malicious page could simply never fire). All this
 * SDK does is tell Kinato a checkout/cart exists; the server takes it from there.
 */
(function (window) {
    'use strict';

    var STORAGE_KEY = 'kinato_retry_buffer_v1';

    function deriveEndpoint() {
        // The endpoint is always the origin this script was loaded from -
        // never a hardcoded default, so a stale/misconfigured install fails
        // loudly (visible in devtools) instead of silently POSTing to
        // localhost in production.
        var scripts = document.getElementsByTagName('script');
        for (var i = 0; i < scripts.length; i++) {
            var src = scripts[i].src || '';
            if (src.indexOf('/sdk/kinato.js') !== -1) {
                return new URL(src).origin + '/api';
            }
        }
        throw new Error('[Kinato SDK] Could not determine API endpoint - is kinato.js loaded via a <script src="..."> tag?');
    }

    function readBuffer() {
        try {
            return JSON.parse(window.localStorage.getItem(STORAGE_KEY) || '[]');
        } catch (e) {
            return [];
        }
    }

    function writeBuffer(items) {
        try {
            window.localStorage.setItem(STORAGE_KEY, JSON.stringify(items.slice(-50)));
        } catch (e) { /* localStorage unavailable (private mode, quota) - buffering degrades silently */ }
    }

    function KinatoClient() {
        this.apiKey = null;
        this.endpoint = null;
        this.externalId = null;
    }

    KinatoClient.prototype.init = function (config) {
        config = config || {};
        if (!config.apiKey) {
            throw new Error('[Kinato SDK] init() requires apiKey (a pk_... publishable key from your Kinato dashboard).');
        }
        this.apiKey = config.apiKey;
        this.endpoint = config.endpoint || deriveEndpoint();
        console.log('[Kinato SDK] Initialized (' + this.endpoint + ')');
        this._flushBuffer();
        window.addEventListener('online', this._flushBuffer.bind(this));
    };

    KinatoClient.prototype.identify = function (customer) {
        customer = customer || {};
        this.externalId = customer.externalId || this.externalId;
        return this._send('customer.identified', {consent: customer.consent || {}}, {
            external_id: this.externalId,
            name: customer.name || '',
            email: customer.email || '',
            phone: customer.phone || '',
        });
    };

    KinatoClient.prototype.track = function (eventName, data) {
        data = data || {};
        var payload = {
            checkout_id: data.checkoutId,
            cart_id: data.cartId,
            amount: data.amount,
            currency: data.currency || 'INR',
            product_ids: data.productIds || [],
            customer_id: this.externalId,
        };
        return this._send(eventName, payload);
    };

    KinatoClient.prototype._send = function (eventType, payload, customer) {
        if (!this.apiKey || !this.endpoint) {
            console.error('[Kinato SDK] track()/identify() called before init(). Call Kinato.init({apiKey}) first.');
            return Promise.resolve();
        }
        var body = {event_type: eventType, payload: payload};
        if (customer) body.customer = customer;

        var self = this;
        return fetch(this.endpoint + '/events', {
            method: 'POST',
            headers: {'Content-Type': 'application/json', 'X-Kinato-Key': this.apiKey},
            body: JSON.stringify(body),
        }).then(function (res) {
            if (!res.ok) throw new Error('HTTP ' + res.status);
            return res.json();
        }).catch(function (err) {
            console.warn('[Kinato SDK] Send failed, buffering for retry: ' + err.message);
            var buffer = readBuffer();
            buffer.push({body: body, ts: Date.now()});
            writeBuffer(buffer);
        });
    };

    KinatoClient.prototype._flushBuffer = function () {
        var buffer = readBuffer();
        if (!buffer.length || !this.apiKey) return;
        writeBuffer([]); // clear optimistically; failed sends re-buffer themselves
        var self = this;
        buffer.forEach(function (item) {
            fetch(self.endpoint + '/events', {
                method: 'POST',
                headers: {'Content-Type': 'application/json', 'X-Kinato-Key': self.apiKey},
                body: JSON.stringify(item.body),
                keepalive: true,
            }).catch(function () {
                var current = readBuffer();
                current.push(item);
                writeBuffer(current);
            });
        });
    };

    // On tab close, use sendBeacon (survives page unload; fetch does not) -
    // best-effort, not the primary abandonment signal (the server sweeper is).
    // sendBeacon cannot carry custom headers, so the key travels in the body
    // instead (api_key field) - safe because pk_ keys are meant to be public
    // (their security is the origin allowlist + restricted event scope, not
    // secrecy - see app/api/events.py).
    window.addEventListener('pagehide', function () {
        var client = window.Kinato;
        if (!client || !client.apiKey || !client.endpoint) return;
        var buffer = readBuffer();
        if (!buffer.length) return;
        buffer.forEach(function (item) {
            var beaconBody = Object.assign({api_key: client.apiKey}, item.body);
            navigator.sendBeacon(
                client.endpoint + '/events',
                new Blob([JSON.stringify(beaconBody)], {type: 'application/json'})
            );
        });
        writeBuffer([]);
    });

    window.Kinato = new KinatoClient();
})(window);
