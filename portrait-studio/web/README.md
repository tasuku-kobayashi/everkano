# portrait-studio web

React 18 + Vite + TypeScript + React Router + TanStack Query + Zustand + Tailwind CSS + react-window. Standalone pnpm project
(`pnpm-workspace.yaml` here keeps it out of the everkano workspace).

```bash
pnpm install
pnpm api:types      # regenerate src/api/types.ts from ../docs/api/openapi.json (run after API schema changes)
pnpm dev            # http://127.0.0.1:5173 (proxies /api to :8000)
pnpm typecheck && pnpm lint && pnpm test && pnpm build
pnpm e2e            # starts the mock ComfyUI + API automatically, writes ../docs/screenshots/
```

Screens: `src/pages/CharactersPage.tsx` (1) · `WizardPage.tsx` (2) · `WorkspacePage.tsx` (3) · `GalleryPage.tsx` (4) · `SettingsPage.tsx` (5).
Shared UX: `components/` (BlurImage = NSFW blur, VramMeter, JobBar = 1 s job polling, ImageGrid = virtualized, ImageViewer = keyboard, CompareTray).
