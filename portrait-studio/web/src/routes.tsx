import { createBrowserRouter, Navigate } from "react-router-dom";
import { AppLayout } from "./components/AppLayout";
import { CharactersPage } from "./pages/CharactersPage";
import { GalleryPage } from "./pages/GalleryPage";
import { SettingsPage } from "./pages/SettingsPage";
import { WizardPage } from "./pages/WizardPage";
import { WorkspacePage } from "./pages/WorkspacePage";

export const router = createBrowserRouter([
  {
    path: "/",
    element: <AppLayout />,
    children: [
      { index: true, element: <CharactersPage /> },
      { path: "characters", element: <Navigate to="/" replace /> },
      { path: "create", element: <WizardPage /> },
      { path: "workspace", element: <WorkspacePage /> },
      { path: "workspace/:characterId", element: <WorkspacePage /> },
      { path: "gallery", element: <GalleryPage /> },
      { path: "settings", element: <SettingsPage /> },
      { path: "*", element: <Navigate to="/" replace /> },
    ],
  },
]);
