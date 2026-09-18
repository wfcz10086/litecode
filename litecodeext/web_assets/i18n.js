(function(){
  window.I18N = {
    messages: {},
    locale: localStorage.getItem('i18n_locale') || 'zh',

    async load() {
      try {
        const [zh, en] = await Promise.all([
          fetch('/assets/messages.zh.json').then(r => r.json()),
          fetch('/assets/messages.en.json').then(r => r.json()),
        ]);
        this.messages = {zh, en};
      } catch (e) {
        console.warn('[i18n] load failed:', e);
      }
    },

    t(key, fallback) {
      const m = this.messages[this.locale] || {};
      return m[key] || fallback || key;
    },

    applyDOM(root) {
      root = root || document;
      root.querySelectorAll('[data-i18n]').forEach(el => {
        const key = el.getAttribute('data-i18n');
        const orig = el.getAttribute('data-i18n-orig') || el.textContent;
        if (!el.hasAttribute('data-i18n-orig')) {
          el.setAttribute('data-i18n-orig', orig);
        }
        el.textContent = this.t(key, orig);
      });
      root.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
        const key = el.getAttribute('data-i18n-placeholder');
        const orig = el.getAttribute('data-i18n-placeholder-orig') || el.getAttribute('placeholder') || '';
        if (!el.hasAttribute('data-i18n-placeholder-orig')) {
          el.setAttribute('data-i18n-placeholder-orig', orig);
        }
        el.setAttribute('placeholder', this.t(key, orig));
      });
    },

    async setLocale(code) {
      this.locale = code;
      localStorage.setItem('i18n_locale', code);
      if (!this.messages[code]) await this.load();
      this.applyDOM();
    },

    async init() {
      await this.load();
      this.applyDOM();
    },
  };

  document.addEventListener('DOMContentLoaded', () => I18N.init());
})();
