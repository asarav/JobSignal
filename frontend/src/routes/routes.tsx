import { Route, Routes } from "react-router-dom";
import { Copy } from "../copy/copy";
import { DiagnosticsRoute } from "../features/diagnostics/DiagnosticsRoute";
import { DiscoverRoute } from "../features/discover/DiscoverRoute";
import { HomeRoute } from "../features/home/HomeRoute";
import { PlaceholderRoute } from "../features/placeholder/PlaceholderRoute";
import { SettingsRoute } from "../features/settings/SettingsRoute";

export interface NavigationItem {
  to: string;
  label: string;
}

export const navigationItems: NavigationItem[] = [
  { to: "/", label: Copy.nav.dashboard },
  { to: "/discover", label: Copy.nav.discover },
  { to: "/search-profiles", label: Copy.nav.searchProfiles },
  { to: "/saved", label: Copy.nav.saved },
  { to: "/applications", label: Copy.nav.applications },
  { to: "/companies", label: Copy.nav.companies },
  { to: "/posting-intelligence", label: Copy.nav.postingIntelligence },
  { to: "/keywords", label: Copy.nav.keywords },
  { to: "/resume", label: Copy.nav.resume },
  { to: "/seen", label: Copy.nav.seen },
  { to: "/settings", label: Copy.nav.settings },
  { to: "/diagnostics", label: Copy.nav.diagnostics },
];

export function AppRoutes() {
  return (
    <Routes>
      <Route path="/" element={<HomeRoute />} />
      <Route path="/discover" element={<DiscoverRoute />} />
      <Route path="/search-profiles" element={<PlaceholderRoute screen="searchProfiles" />} />
      <Route path="/saved" element={<PlaceholderRoute screen="saved" />} />
      <Route path="/applications" element={<PlaceholderRoute screen="applications" />} />
      <Route path="/companies" element={<PlaceholderRoute screen="companies" />} />
      <Route path="/companies/:id" element={<PlaceholderRoute screen="companies" />} />
      <Route
        path="/posting-intelligence"
        element={<PlaceholderRoute screen="postingIntelligence" />}
      />
      <Route path="/keywords" element={<PlaceholderRoute screen="keywords" />} />
      <Route path="/resume" element={<PlaceholderRoute screen="resume" />} />
      <Route path="/seen" element={<PlaceholderRoute screen="seen" />} />
      <Route path="/jobs/:id" element={<PlaceholderRoute screen="job" />} />
      <Route path="/settings" element={<SettingsRoute />} />
      <Route path="/diagnostics" element={<DiagnosticsRoute />} />
      <Route path="*" element={<PlaceholderRoute screen="job" />} />
    </Routes>
  );
}
