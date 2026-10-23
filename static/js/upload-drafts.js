/* Private progressive uploads. A file selection never submits a business action. */
(function (global) {
  'use strict';
  const noop = () => {};
  function recoveryUrl(current, identity, slot) {
    const url = new URL(current);
    url.searchParams.set('upload_draft', identity);
    if (slot) url.searchParams.set(slot, identity);
    return url.href;
  }

  class UploadQueue {
    constructor(options = {}) {
      this.transport = options.transport;
      this.changed = options.changed || noop;
      this.announce = options.announce || noop;
      this.publishDraft = options.publishDraft || noop;
      this.readValues = options.readValues;
      this.createId = options.createId || (() => global.crypto.randomUUID());
      this.id = options.id || '';
      this.revision = Number(options.revision || 0);
      this.rows = new Map();
      for (const item of options.existing || []) {
        const key = item.client_upload_id || `saved-${item.id}`;
        this.rows.set(key, {...item, key, clientId: key, state: 'saved', file: null, removeRequested: false});
      }
      this.tail = Promise.resolve();
      this.disposed = false;
      this.submitting = null;
      this.submitted = false;
      this.stateUncertain = false;
    }
    emit() {if (!this.disposed) this.changed(this.rows, this);}
    say(message) {if (!this.disposed) this.announce(message);}
    enqueue(operation) {
      this.tail = this.tail.then(() => this.disposed ? undefined : operation()).catch(error => this.say(error.message || 'Upload unavailable. Retry.'));
      return this.tail;
    }
    accept(answer) {
      if (!answer || !answer.id || !Number.isInteger(answer.revision) || answer.revision < this.revision || (this.id && this.id !== answer.id)) {
        throw new Error('The saved upload changed. Retry to check its current state.');
      }
      const first = !this.id;
      this.id = answer.id;
      this.revision = answer.revision;
      for (const item of answer.files || []) {
        let row = [...this.rows.values()].find(candidate => candidate.clientId === item.client_upload_id || candidate.id === item.id);
        if (!row) {
          const key = item.client_upload_id || `saved-${item.id}`;
          row = {key, clientId: key, state: 'saved', file: null, removeRequested: false};
          this.rows.set(key, row);
        }
        Object.assign(row, item);
        if (!row.removeRequested) row.state = 'saved';
      }
      if (first && !this.disposed) this.publishDraft(answer);
      return answer;
    }
    async ensureDraft() {if (!this.id) this.accept(await this.transport('open', {}));}
    add(files) {
      const keys = [];
      for (const selected of files) {
        const key = this.createId(); keys.push(key);
        this.rows.set(key, {key, clientId: key, file: selected, name: selected.name, size: selected.size,
          state: 'pending', message: '', removeRequested: false});
        this.enqueue(() => this.upload(key));
      }
      if (this.readValues) this.enqueue(() => this.saveValues());
      this.emit();
      return keys;
    }
    async upload(key) {
      const row = this.rows.get(key);
      if (!row || row.removeRequested) {if (row) row.state = 'removed'; this.emit(); return;}
      row.state = 'uploading'; row.message = ''; this.emit(); this.say('Saving files…');
      try {
        await this.ensureDraft();
        let answer;
        try {
          answer = this.accept(await this.transport('upload', {id: this.id, file: row.file,
            client_upload_id: row.clientId, expected_revision: this.revision}));
        } catch (error) {
          // A timed-out response may already have committed. Reconcile the
          // opaque client identity before offering any retry of the write.
          answer = this.accept(await this.transport('read', {id: this.id}));
          if (!(answer.files || []).some(item => item.client_upload_id === row.clientId)) throw error;
        }
        if (this.disposed) return;
        const saved = (answer.files || []).find(item => item.client_upload_id === row.clientId);
        if (!saved) throw new Error(answer.errors?.[0]?.message || 'This file was not saved. Retry or remove it.');
        Object.assign(row, saved);
        row.state = row.removeRequested ? 'removing' : 'saved';
        row.file = null;
        this.say('Files saved.');
      } catch (error) {
        if (!this.disposed) {row.state = row.removeRequested && !row.id ? 'removed' : 'error'; row.message = error.message || 'File not saved. Retry.'; this.say(row.message);}
      }
      this.emit();
    }
    retry(key) {
      const row = this.rows.get(key);
      if (!row || row.state !== 'error') return;
      this.enqueue(() => row.removeRequested ? this.removeSaved(row) : this.upload(key));
      if (this.readValues) this.enqueue(() => this.saveValues());
    }
    remove(key) {
      const row = this.rows.get(key);
      if (!row || row.state === 'removed') return;
      row.removeRequested = true; row.state = 'removing'; this.emit();
      this.enqueue(() => this.removeSaved(row));
    }
    async removeSaved(row) {
      try {
        if (row.id) {
          try {
            this.accept(await this.transport('remove', {id: this.id, file_id: row.id, expected_revision: this.revision}));
          } catch (error) {
            const current = this.accept(await this.transport('read', {id: this.id}));
            if ((current.files || []).some(item => item.id === row.id)) throw error;
          }
        }
        if (this.disposed) return;
        row.state = 'removed'; row.file = null; row.message = ''; this.say('File removed from this upload.');
      } catch (error) {if (!this.disposed) {row.state = 'error'; row.message = 'File removal failed. Retry.'; this.say(row.message);}}
      this.emit();
    }
    async saveValues() {
      if (!this.id || this.disposed) return;
      try {this.accept(await this.transport('save', {id: this.id, expected_revision: this.revision, values: this.readValues()})); this.stateUncertain = false;}
      catch (error) {
        try {this.accept(await this.transport('read', {id: this.id})); this.stateUncertain = false;}
        catch (failure) {this.stateUncertain = true; this.say('Files remain saved. Retry when the connection returns.');}
      }
      this.emit();
    }
    async whenIdle() {
      let pending;
      do {pending = this.tail; await pending;} while (pending !== this.tail);
    }
    submit(callback) {
      if (this.submitted) return Promise.resolve(true);
      if (this.submitting) return this.submitting;
      this.submitting = (async () => {
        await this.whenIdle();
        if (this.stateUncertain) {
          await this.enqueue(async () => {
            try {this.accept(await this.transport('read', {id: this.id})); this.stateUncertain = false; this.emit();}
            catch (error) {this.say('The saved form could not be refreshed. Retry when the connection returns.');}
          });
          await this.whenIdle();
          if (this.stateUncertain) return false;
        }
        if (this.disposed) return false;
        if ([...this.rows.values()].some(row => row.state === 'error')) {this.say('Retry or remove files that were not saved.'); return false;}
        this.submitted = true;
        callback();
        return true;
      })().finally(() => {this.submitting = null;});
      return this.submitting;
    }
    dispose() {this.disposed = true;}
  }

  function initialize(root) {
    if (!global.document || !global.fetch || !global.FormData || !global.crypto?.randomUUID) return;
    const regions = [...(root.querySelectorAll?.('[data-upload-draft]') || [])];
    if (root.matches?.('[data-upload-draft]')) regions.unshift(root);
    for (const region of regions) enhance(region);
  }
  const controllers = new WeakMap();
  function enhance(region) {
    if (controllers.has(region)) return;
    const form = region.closest('form');
    if (!form) return;
    const input = form.elements.namedItem(region.dataset.uploadField);
    if (!input || input.type !== 'file') return;
    const status = region.querySelector('[data-upload-status]'), list = region.querySelector('[data-upload-file-list]');
    const field = name => form.elements.namedItem(name);
    const abort = new AbortController();
    let latestKeys = [], selectionError = '', sessionExpired = false;
    const existing = [...list.querySelectorAll('[data-saved-upload]')].map(element => ({
      id: element.dataset.savedUpload, client_upload_id: element.dataset.clientUploadId, name: element.dataset.fileName,
      size: Number(element.dataset.fileSize || 0), preview_url: element.querySelector('a')?.href,
    }));
    const announce = text => {status.textContent = text;};
    const transport = async (action, payload) => {
      let url = '/uploads/drafts/', method = 'POST';
      const body = new FormData();
      if (action === 'open') {
        body.append('workflow', region.dataset.uploadWorkflow);
        body.append('form_nonce', field('upload_form_nonce').value);
        if (region.dataset.uploadTarget) body.append('target_id', region.dataset.uploadTarget);
        if (region.dataset.uploadGroup) body.append('group_id', region.dataset.uploadGroup);
        const query = new URL(global.location.href).searchParams;
        const office = field('office') || field('managing_office');
        if (query.has('office')) url += `?${new URLSearchParams(query.getAll('office').map(value => ['office', value]))}`;
        else if (office?.value) url += `?office=${encodeURIComponent(office.value)}`;
      } else {
        url += `${encodeURIComponent(payload.id)}/`;
        if (action === 'read') {url += '?format=json'; method = 'GET';}
        if (action === 'upload') {url += 'files/'; body.append('file', payload.file); body.append('client_upload_id', payload.client_upload_id);}
        if (action === 'remove') url += `files/${encodeURIComponent(payload.file_id)}/remove/`;
        if (action === 'save') body.append('values', JSON.stringify(payload.values));
        if (method === 'POST') body.append('expected_revision', payload.expected_revision);
      }
      const response = await global.fetch(url, {method, body: method === 'POST' ? body : undefined,
        credentials: 'same-origin', cache: 'no-store', signal: abort.signal,
        headers: {'X-CSRFToken': field('csrfmiddlewaretoken').value, 'Accept': 'application/json'}});
      if (response.redirected || !(response.headers.get('Content-Type') || '').includes('application/json')) {
        sessionExpired = response.redirected && new URL(response.url).pathname.includes('/accounts/login');
        throw new Error(sessionExpired ? 'Your session expired. Sign in, then retry.' : 'Upload unavailable. Retry or remove the unsaved file.');
      }
      const data = await response.json();
      if (!response.ok) throw new Error(data.errors?.join(' ') || 'Upload unavailable. Retry.');
      return data;
    };
    const button = (label, kind, key) => {
      const control = document.createElement('button'); control.type = 'button'; control.className = 'btn btn-udm-outline btn-sm';
      control.textContent = label; control.dataset[kind] = key; return control;
    };
    const paint = (rows, queue) => {
      field('upload_draft').value = queue.id;
      field('upload_revision').value = queue.revision;
      list.replaceChildren();
      for (const row of rows.values()) {
        if (row.state === 'removed') continue;
        const item = document.createElement('li'); item.className = 'file-chip my-2 flex-wrap'; item.dataset.savedUpload = row.id || '';
        const copy = document.createElement('div'); copy.className = 'flex-grow-1 text-break';
        const name = document.createElement('strong'); name.textContent = row.name;
        const label = document.createElement('span'); label.className = 'badge ms-2 ' + (row.state === 'saved' ? 'text-bg-success' : 'text-bg-secondary');
        label.textContent = {pending: 'Waiting', uploading: 'Uploading', saved: 'Saved', error: 'Not saved', removing: 'Removing'}[row.state] || row.state;
        copy.append(name, label);
        if (row.message) {const error = document.createElement('div'); error.className = 'small text-danger'; error.textContent = row.message; copy.append(error);}
        item.append(copy);
        if (row.state === 'saved' && row.preview_url) {
          const preview = document.createElement('a'); const target = new URL(row.preview_url, global.location.href);
          if (target.origin === global.location.origin) {preview.href = target.href; preview.textContent = 'Preview'; preview.className = 'btn btn-udm-outline btn-sm'; item.append(preview);}
        }
        if (row.state === 'error') item.append(button('Retry', 'uploadRetry', row.key));
        if (!row.removeRequested) item.append(button('Remove', 'uploadRemove', row.key));
        list.append(item);
      }
      if (latestKeys.length && latestKeys.every(key => ['saved', 'removed'].includes(queue.rows.get(key)?.state))) {input.value = ''; latestKeys = [];}
      if (sessionExpired && !region.querySelector('[data-upload-login]')) {
        const login = document.createElement('a'); login.dataset.uploadLogin = ''; login.href = `/accounts/login/?next=${encodeURIComponent(global.location.pathname + global.location.search)}`;
        login.textContent = 'Sign in'; login.className = 'btn btn-udm-outline btn-sm'; region.append(login);
      }
    };
    const queue = new UploadQueue({id: field('upload_draft').value, revision: Number(field('upload_revision').value || 0),
      existing, transport, announce, changed: paint,
      publishDraft: data => {
        const next = new URL(recoveryUrl(global.location.href, data.id, region.dataset.uploadSlot));
        if (data.form_url) {const canonical = new URL(data.form_url, global.location.href); if (canonical.searchParams.has('office')) next.searchParams.set('office', canonical.searchParams.get('office'));}
        global.history.replaceState(global.history.state, '', next.href);
      }, readValues: () => {
        const values = {};
        for (const [name, value] of new FormData(form)) if (typeof value === 'string') (values[name] ||= []).push(value);
        return values;
      }});
    controllers.set(region, {queue, abort});
    input.addEventListener('change', () => {
      const files = [...input.files]; latestKeys = []; selectionError = '';
      const limit = Number(region.dataset.uploadLimit || 10);
      const used = [...queue.rows.values()].filter(row => row.state !== 'removed').length;
      if (files.length + used > limit) {selectionError = `This form accepts up to ${limit} file(s). Remove a file or choose fewer files.`; announce(selectionError); return;}
      latestKeys = queue.add(files);
    });
    region.addEventListener('click', event => {
      const retry = event.target.closest('[data-upload-retry]'), remove = event.target.closest('[data-upload-remove]');
      if (retry) {event.preventDefault(); queue.retry(retry.dataset.uploadRetry);}
      if (remove) {event.preventDefault(); queue.remove(remove.dataset.uploadRemove);}
    });
    form.addEventListener('submit', async event => {
      // Before enhancement has done anything, retain normal multipart fallback.
      if (!queue.id && !queue.rows.size && !selectionError) return;
      if (event.submitter?.name === 'remove_upload') return;
      event.preventDefault();
      if (selectionError) {announce(selectionError); input.focus(); return;}
      const submitted = await queue.submit(() => HTMLFormElement.prototype.submit.call(form));
      if (!submitted) {region.tabIndex = -1; region.focus({preventScroll: true}); region.scrollIntoView({block: 'center', behavior: 'instant'});}
    });
    paint(queue.rows, queue);
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = {UploadQueue, recoveryUrl};
  if (global.document) {
    initialize(document);
    document.addEventListener('htmx:load', event => initialize(event.detail?.elt || document));
    document.addEventListener('htmx:beforeCleanupElement', event => {
      const root = event.detail?.elt;
      if (!root) return;
      const regions = [...(root.querySelectorAll?.('[data-upload-draft]') || [])];
      if (root.matches?.('[data-upload-draft]')) regions.push(root);
      for (const region of regions) {const current = controllers.get(region); if (current) {current.queue.dispose(); current.abort.abort(); controllers.delete(region);}}
    });
  }
})(typeof globalThis !== 'undefined' ? globalThis : window);
