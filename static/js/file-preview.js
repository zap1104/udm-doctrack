// No document scripts, annotation actions, macros or remote document URLs run here.
const root = document.querySelector('[data-file-preview]');
if (root && ['pdf', 'docx', 'image'].includes(root.dataset.kind)) {
  const status = root.querySelector('[data-preview-status]');
  const surface = root.querySelector('[data-preview-surface]');
  const toolbar = root.querySelector('[data-preview-toolbar]');
  const vendor = root.dataset.vendorRoot;
  let zoom = 1;
  let renderZoom;
  function failed() {
    status.textContent = 'Preview unavailable. Download the original file, or return to the record for a fresh link.';
    surface.replaceChildren();
    toolbar.hidden = true;
  }
  async function start() {
    const response = await fetch(root.dataset.contentUrl, { credentials: 'same-origin', redirect: 'error' });
    if (!response.ok) throw new Error('Preview request failed');
    const bytes = await response.arrayBuffer();
    if (root.dataset.kind === 'pdf') {
      const pdfjs = await import(`${vendor}pdfjs-dist/build/pdf.mjs`);
      pdfjs.GlobalWorkerOptions.workerSrc = `${vendor}pdfjs-dist/build/pdf.worker.mjs`;
      const pdf = await pdfjs.getDocument({ data: new Uint8Array(bytes), isEvalSupported: false,
        enableXfa: false, useWasm: false, maxImageSize: 16_000_000, canvasMaxAreaInBytes: 64_000_000,
        cMapUrl: `${vendor}pdfjs-dist/cmaps/`, cMapPacked: true,
        standardFontDataUrl: `${vendor}pdfjs-dist/standard_fonts/`, iccUrl: `${vendor}pdfjs-dist/iccs/` }).promise;
      let pageNumber = 1;
      let rendering = false;
      async function showPage() {
        if (rendering) return;
        rendering = true;
        const controls = [...toolbar.querySelectorAll('button')];
        controls.forEach(button => { button.disabled = true; });
        try {
          const page = await pdf.getPage(pageNumber);
          const natural = page.getViewport({ scale: 1 });
          const fit = Math.min(1, Math.max(250, surface.clientWidth - 28) / natural.width);
          const viewport = page.getViewport({ scale: fit * zoom });
          if (viewport.width * viewport.height > 16_000_000) throw new Error('Page too large');
          const wrapper = document.createElement('div'); wrapper.className = 'preview-page';
          wrapper.style.setProperty('--scale-factor', viewport.scale);
          const canvas = document.createElement('canvas');
          canvas.width = Math.ceil(viewport.width); canvas.height = Math.ceil(viewport.height);
          canvas.setAttribute('aria-label', `Page ${pageNumber} of ${pdf.numPages}`);
          wrapper.append(canvas); surface.replaceChildren(wrapper);
          await page.render({ canvasContext: canvas.getContext('2d'), viewport }).promise;
          const layer = document.createElement('div'); layer.className = 'textLayer'; wrapper.append(layer);
          await new pdfjs.TextLayer({ textContentSource: await page.getTextContent(), container: layer, viewport }).render();
          root.querySelector('[data-page-count]').textContent = `Page ${pageNumber} of ${pdf.numPages}`;
        } finally {
          rendering = false;
          controls.forEach(button => { button.disabled = false; });
          root.querySelector('[data-prev]').disabled = pageNumber === 1;
          root.querySelector('[data-next]').disabled = pageNumber === pdf.numPages;
        }
      }
      root.querySelector('[data-prev]').addEventListener('click', () => { pageNumber--; showPage().catch(failed); });
      root.querySelector('[data-next]').addEventListener('click', () => { pageNumber++; showPage().catch(failed); });
      renderZoom = showPage;
      await showPage();
    } else if (root.dataset.kind === 'docx') {
      const frame = document.createElement('iframe');
      frame.title = 'Approximate DOCX preview'; frame.setAttribute('sandbox', 'allow-same-origin');
      const loaded = new Promise((resolve, reject) => { frame.onload = resolve; frame.onerror = reject; });
      frame.src = root.dataset.frameUrl; surface.append(frame); await loaded;
      const doc = frame.contentDocument;
      if (!doc || !doc.body) throw new Error('Sandbox unavailable');
      await window.docx.renderAsync(bytes, doc.body, doc.head, { renderAltChunks: false,
        experimental: false, useBase64URL: true, ignoreWidth: true, breakPages: true, renderComments: false });
      // Relationships were already stripped server-side; make links inert too.
      doc.querySelectorAll('a').forEach(link => { link.removeAttribute('href'); link.removeAttribute('target'); });
    } else {
      const image = document.createElement('img'); image.alt = 'Read-only uploaded image';
      const url = URL.createObjectURL(new Blob([bytes], { type: response.headers.get('Content-Type') }));
      image.src = url; surface.append(image);
      await image.decode();
      const initial = Math.min(image.naturalWidth, Math.max(240, surface.clientWidth - 28));
      renderZoom = async () => { image.width = Math.round(initial * zoom); };
      await renderZoom();
      window.addEventListener('pagehide', () => URL.revokeObjectURL(url), { once: true });
    }
    if (renderZoom) {
      for (const [selector, delta] of [['[data-zoom-in]', .25], ['[data-zoom-out]', -.25]]) {
        root.querySelector(selector).addEventListener('click', () => {
          zoom = Math.max(.5, Math.min(3, zoom + delta));
          root.querySelector('[data-zoom]').textContent = `${Math.round(zoom * 100)}%`;
          renderZoom().catch(failed);
        });
      }
    }
    toolbar.hidden = root.dataset.kind === 'docx';
    status.textContent = 'Preview ready. The original file is unchanged.';
  }
  start().catch(failed);
}
