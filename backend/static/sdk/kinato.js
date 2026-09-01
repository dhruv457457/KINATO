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
 * AUTO-CAPTURE: if the page uses Razorpay Checkout, none of the calls above
 * are required. The SDK wraps `new Razorpay(options)` and reads the amount,
 * currency, order id and prefilled contact details that are already there,
 * so the script tag alone is a working install. It never fabricates consent:
 * an auto-captured customer has none, and outreach stays blocked until a
 * real grant exists. Opt out with data-auto="off" on the script tag.
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

    // --- Auto-capture from Razorpay Checkout -------------------------
    //
    // Everything above works, and it still asks the merchant to write
    // identify() and track() calls into their own checkout code. That is
    // the actual integration cost - not the script tag - and it is why a
    // storefront can have Kinato "installed" and send nothing.
    //
    // But every Razorpay integration opens checkout the same way:
    //
    //     var rzp = new Razorpay(options); rzp.open();
    //
    // and `options` already holds the amount, the currency, the order id
    // and the customer's prefilled name, email and phone. So we wrap the
    // constructor and read what is already there. No merchant code.
    //
    // TWO THINGS THIS DELIBERATELY DOES NOT DO.
    //
    // It does not invent consent. Razorpay's options carry no permission to
    // phone anybody, so nothing here sets one - the customer is identified
    // with `consent: {}`, and recovery_eligibility blocks outreach until a
    // real grant exists. Auto-capture gets us the cart; it does not get us
    // the right to call about it.
    //
    // And it does not decide abandonment. That is the server's durable
    // sweeper, for the reason at the top of this file: a client-side timer
    // is a promise made by a tab that may already be closed.
    function wrapRazorpay(Original) {
        if (!Original || Original.__kinatoWrapped) return Original;

        function Wrapped(options) {
            try {
                captureCheckout(options || {});
            } catch (e) {
                // A tracking failure must never stop a customer paying.
                if (window.console) console.warn('[Kinato SDK] auto-capture skipped: ' + e.message);
            }
            return new Original(options);
        }

        Wrapped.prototype = Original.prototype;
        Wrapped.__kinatoWrapped = true;
        // Razorpay hangs helpers off the constructor; carry them across so
        // wrapping is invisible to anything already using them.
        for (var k in Original) {
            if (Object.prototype.hasOwnProperty.call(Original, k)) Wrapped[k] = Original[k];
        }
        return Wrapped;
    }

    function captureCheckout(options) {
        var client = window.Kinato;
        if (!client || !client.apiKey || client.autoCapture === false) return;

        var prefill = options.prefill || {};
        var notes = options.notes || {};

        // amount arrives in PAISE, as Razorpay takes it; the events API
        // takes rupees. Converting here rather than sending the bigger
        // number and hoping the server guesses which unit it is in.
        var amountPaise = typeof options.amount === 'number' ? options.amount : parseInt(options.amount, 10);
        var amount = isFinite(amountPaise) ? amountPaise / 100 : undefined;

        if (prefill.email || prefill.contact) {
            client.identify({
                externalId: notes.customer_id || prefill.email || prefill.contact,
                name: prefill.name || '',
                email: prefill.email || '',
                phone: prefill.contact || '',
                // Empty on purpose. See above.
                consent: {}
            });
        }

        client.track('checkout.started', {
            checkoutId: options.order_id || notes.checkout_id || ('rzp_' + Date.now()),
            amount: amount,
            currency: options.currency || 'INR',
            productIds: notes.product_ids ? String(notes.product_ids).split(',') : []
        });
    }

    window.Kinato = new KinatoClient();

    // Razorpay's script may load before or after ours, so handle both: wrap
    // it if it is already there, and otherwise wait for the property to be
    // defined and wrap it then.
    try {
        if (window.Razorpay) {
            window.Razorpay = wrapRazorpay(window.Razorpay);
        } else {
            var pending;
            Object.defineProperty(window, 'Razorpay', {
                configurable: true,
                get: function () { return pending; },
                set: function (value) { pending = wrapRazorpay(value); }
            });
        }
    } catch (e) {
        /* a host page that has locked down window.Razorpay keeps working */
    }


    // Auto-initialise from the script tag's own data-key attribute, so
    //   <script src=".../sdk/kinato.js" data-key="pk_test_..."></script>
    // is genuinely all a merchant needs. Without this the onboarding
    // snippet was inert: the tag loaded, the key was ignored, init() was
    // never called, and every track() silently no-op'd - the worst kind of
    // failure, because the merchant sees no error at all. An explicit
    // Kinato.init({apiKey}) call still works and overrides this.
    try {
        var tag = document.currentScript;
        if (!tag) {
            // currentScript is null for async/deferred loads - fall back to
            // locating our own tag by src.
            var scripts = document.getElementsByTagName('script');
            for (var i = 0; i < scripts.length; i++) {
                if ((scripts[i].src || '').indexOf('kinato.js') !== -1) { tag = scripts[i]; break; }
            }
        }
        var autoKey = tag && tag.getAttribute('data-key');
        if (tag && tag.getAttribute('data-auto') === 'off') {
            window.Kinato.autoCapture = false;
        }
        if (autoKey && autoKey.indexOf('pk_') === 0) {
            window.Kinato.init({ apiKey: autoKey });
        }
    } catch (e) {
        /* never let auto-init break the host page */
    }
})(window);
