import { Route, Routes } from "react-router-dom";
import { Copy } from "../copy/copy";
import { ApplicationsRoute } from "../features/applications/ApplicationsRoute";
import { CompaniesRoute } from "../features/companies/CompaniesRoute";
import { DiagnosticsRoute } from "../features/diagnostics/DiagnosticsRoute";
import { DiscoverRoute } from "../features/discover/DiscoverRoute";
import { HomeRoute } from "../features/home/HomeRoute";
import { JobDetailRoute } from "../features/jobs/JobDetailRoute";
import { KeywordsRoute } from "../features/keywords/KeywordsRoute";
import { PostingIntelligenceRoute } from "../features/posting-intelligence/PostingIntelligenceRoute";
import { SavedRoute } from "../features/saved/SavedRoute";
import { SeenRoute } from "../features/seen/SeenRoute";
import { PlaceholderRoute } from "../features/placeholder/PlaceholderRoute";
import { ResumeRoute } from "../features/resume/ResumeRoute";
import { SearchRoute } from "../features/search/SearchRoute";
import { SettingsRoute } from "../features/settings/SettingsRoute";

export interface NavigationItem {
  to: string;
  label: string;
  description: string;
}

export const navigationItems: NavigationItem[] = [
  { to: "/", label: Copy.nav.dashboard, description: Copy.navDescriptions.dashboard },
  { to: "/discover", label: Copy.nav.discover, description: Copy.navDescriptions.discover },
  {
    to: "/search-profiles",
    label: Copy.nav.searchProfiles,
    description: Copy.navDescriptions.searchProfiles,
  },
  { to: "/saved", label: Copy.nav.saved, description: Copy.navDescriptions.saved },
  {
    to: "/applications",
    label: Copy.nav.applications,
    description: Copy.navDescriptions.applications,
  },
  { to: "/companies", label: Copy.nav.companies, description: Copy.navDescriptions.companies },
  {
    to: "/posting-intelligence",
    label: Copy.nav.postingIntelligence,
    description: Copy.navDescriptions.postingIntelligence,
  },
  { to: "/keywords", label: Copy.nav.keywords, description: Copy.navDescriptions.keywords },
  { to: "/resume", label: Copy.nav.resume, description: Copy.navDescriptions.resume },
  { to: "/seen", label: Copy.nav.seen, description: Copy.navDescriptions.seen },
  { to: "/settings", label: Copy.nav.settings, description: Copy.navDescriptions.settings },
  {
    to: "/diagnostics",
    label: Copy.nav.diagnostics,
    description: Copy.navDescriptions.diagnostics,
  },
];

export function AppRoutes() {
  return (
    <Routes>
      <Route path="/" element={<HomeRoute />} />
      <Route path="/discover" element={<DiscoverRoute />} />
      <Route path="/search-profiles" element={<SearchRoute />} />
      <Route path="/saved" element={<SavedRoute />} />
      <Route path="/applications" element={<ApplicationsRoute />} />
      <Route path="/companies" element={<CompaniesRoute />} />
      <Route path="/companies/:id" element={<CompaniesRoute />} />
      <Route path="/posting-intelligence" element={<PostingIntelligenceRoute />} />
      <Route path="/keywords" element={<KeywordsRoute />} />
      <Route path="/resume" element={<ResumeRoute />} />
      <Route path="/seen" element={<SeenRoute />} />
      <Route path="/jobs/:id" element={<JobDetailRoute />} />
      <Route path="/settings" element={<SettingsRoute />} />
      <Route path="/diagnostics" element={<DiagnosticsRoute />} />
      <Route path="*" element={<PlaceholderRoute screen="job" />} />
    </Routes>
  );
}
