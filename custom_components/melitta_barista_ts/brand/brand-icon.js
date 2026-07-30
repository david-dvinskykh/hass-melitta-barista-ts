/*
 * Puts the Melitta artwork on the integrations and devices pages.
 *
 * Home Assistant builds the tile's URL from the domain and fetches it from
 * brands.home-assistant.io. Nothing an integration does can redirect that,
 * so this module waits for the image to appear in the page and points it at
 * the copy the integration serves itself.
 *
 * Loaded only when the "Serve the Melitta logo" option is on. It is a patch
 * on someone else's markup, so it is written to do nothing at all rather
 * than risk the page: it only ever rewrites the src of an img that already
 * points at this domain's brand image, and gives up quietly otherwise.
 */

const DOMAIN = "melitta_barista_ts";
const LOCAL = `/${DOMAIN}_brand/`;
const CDN = "brands.home-assistant.io";
const RESCAN_DELAY = 250;

/** The local file that replaces a brands URL, or null to leave it alone. */
function replacement(src) {
  if (typeof src !== "string" || !src.includes(CDN) || !src.includes(DOMAIN)) {
    return null;
  }
  // .../melitta_barista_ts/dark_icon@2x.png → icon@2x.png. The wordmark reads
  // on light and dark alike, so both themes get the same file.
  const name = src.slice(src.lastIndexOf("/") + 1).replace(/^dark_/, "");
  if (!/^(icon|logo)(@2x)?\.png$/.test(name)) {
    return null;
  }
  return LOCAL + name;
}

const seen = new WeakSet();
let pending = null;

function scan(root) {
  let images;
  try {
    images = root.querySelectorAll("img");
  } catch {
    return;
  }

  for (const img of images) {
    const next = replacement(img.getAttribute("src"));
    if (next) {
      img.setAttribute("src", next);
    }
  }

  // The tile lives several shadow roots deep, and each one needs watching of
  // its own: a mutation inside a shadow root does not surface in the parent.
  for (const element of root.querySelectorAll("*")) {
    if (element.shadowRoot) {
      watch(element.shadowRoot);
    }
  }
}

function rescan() {
  if (pending !== null) {
    return;
  }
  pending = setTimeout(() => {
    pending = null;
    scan(document);
  }, RESCAN_DELAY);
}

function watch(root) {
  if (seen.has(root)) {
    return;
  }
  seen.add(root);
  new MutationObserver(rescan).observe(root, { childList: true, subtree: true });
  scan(root);
}

watch(document);
