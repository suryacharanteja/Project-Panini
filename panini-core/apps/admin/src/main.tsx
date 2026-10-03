import { createRootRoute, createRoute, createRouter, RouterProvider } from '@tanstack/react-router';
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { Shell } from './components/Shell';
import { ToastProvider } from './components/ui';
import { AgentsPage, ConnectorsPage, SkillsPage } from './pages/Build';
import { CapabilitiesPage } from './pages/Capabilities';
import { ComputersPage } from './pages/Computers';
import { LivePage } from './pages/Live';
import { SafetyPage } from './pages/Safety';
import { RemotePage } from './pages/Remote';
import { ConversationsPage } from './pages/Conversations';
import { PermissionsPage } from './pages/Permissions';
import { ReceiptsPage } from './pages/Receipts';
import {
  AuditPage,
  CapabilityPage,
  DevicesPage,
  KeysPage,
  NotFound,
  PlaygroundPage,
  SystemPage,
  UsagePage,
  LearningPage,
} from './pages/Config';
import { Home } from './pages/Home';
import { ProjectsPage } from './pages/Projects';
import { AutomationsPage, InboxPage, MemoryPage, RunsPage } from './pages/Work';
import './styles.css';

const root = createRootRoute({ component: Shell, notFoundComponent: NotFound });
const page = (path: string, component: () => React.ReactNode) =>
  createRoute({ getParentRoute: () => root, path, component });

const routeTree = root.addChildren([
  page('/', Home),
  page('/inbox', InboxPage),
  page('/conversations', ConversationsPage),
  page('/runs', RunsPage),
  page('/receipts', ReceiptsPage),
  page('/projects', ProjectsPage),
  page('/build/skills', SkillsPage),
  page('/build/connectors', ConnectorsPage),
  page('/build/automations', AutomationsPage),
  page('/build/agents', AgentsPage),
  page('/build/capabilities', CapabilitiesPage),
  page('/build/computers', ComputersPage),
  page('/build/live', LivePage),
  page('/build/safety', SafetyPage),
  page('/build/remote', RemotePage),
  page('/build/permissions', PermissionsPage),
  page('/memory', MemoryPage),
  page('/capabilities/$name', CapabilityPage),
  page('/playground', PlaygroundPage),
  page('/settings/keys', KeysPage),
  page('/settings/devices', DevicesPage),
  page('/settings/system', SystemPage),
  page('/settings/usage', UsagePage),
  page('/settings/learning', LearningPage),
  page('/audit', AuditPage),
]);

const router = createRouter({ routeTree, defaultPreload: 'intent' });

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ToastProvider>
      <RouterProvider router={router} />
    </ToastProvider>
  </StrictMode>,
);
