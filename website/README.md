# Hermes landing page

A self-contained static landing page in `index.html`. It has no package dependencies, external fonts, tracking, build step, or live API connections. The interface preview and its content are explicitly illustrative.

## Preview

Open `website/index.html` directly in a browser, or from the repository root run:

```sh
python3 -m http.server 8000 --directory website
```

Visit `http://localhost:8000`. Clipboard access uses the browser clipboard API where supported and selects the installation commands for manual copying otherwise.

## Deploy

Serve the contents of `website/` with any static hosting provider. Use `website` as the publish directory and no build command. All assets are embedded, so the page also supports subdirectory hosting.

No hosting configuration or production deployment is included in this change. The existing native app and dashboard are unaffected.

## Included interactions

- Dark, Light, and Auto themes with persisted preference; Auto follows the operating system.
- Reduce motion preference, also respecting the system motion setting.
- Keyboard accessible Hub, Needs you, and Mind demo tabs.
- Illustrative approve/deny flow, without invoking any app action.
- Sample context search, rendered with text nodes instead of user-supplied HTML.
- Copy installation commands with a manual selection fallback.
- Native FAQ disclosure controls, sticky navigation, and pointer lighting on glass cards.

## Content

Installation, requirements, license, and v1.3.2 details are grounded in the repository README and VERSION at creation. Privacy copy distinguishes local inference from external feeds, web tools, connected services, Telegram, optional Claude usage, and MCP client access. The call to action leads to source installation, not an unavailable packaged DMG.

## Verification

JavaScript parses successfully. HTML IDs are unique and all internal fragment links resolve. The committed file was read back from GitHub. Browser rendering, responsive layouts, interaction behavior, and deployment have not been verified because no local execution environment was available.

Before publishing, review at desktop and mobile widths, tab through all controls, exercise themes and reduced motion, test the copy fallback, and review product links.
