import { BrowserRouter, Route, Routes } from 'react-router-dom';
import Layout from './components/Layout';
import DashboardPage from './pages/DashboardPage';
import CreateCasePage from './pages/CreateCasePage';
import CaseDetailPage from './pages/CaseDetailPage';
import DemoPage from './pages/DemoPage';
import SafetyPage from './pages/SafetyPage';

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route path="/" element={<DashboardPage />} />
          <Route path="/create" element={<CreateCasePage />} />
          <Route path="/cases/:caseId" element={<CaseDetailPage />} />
          <Route path="/demo" element={<DemoPage />} />
          <Route path="/safety" element={<SafetyPage />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
