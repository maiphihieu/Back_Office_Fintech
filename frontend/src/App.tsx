import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import Layout from './components/Layout';
import DashboardPage from './pages/DashboardPage';
import CreateCasePage from './pages/CreateCasePage';
import CaseDetailPage from './pages/CaseDetailPage';
import SafetyPage from './pages/SafetyPage';
import CustomerLoginPage from './pages/CustomerLoginPage';
import CustomerChatTicketsPage from './pages/CustomerChatTicketsPage';
import ChatTicketDetailPage from './pages/ChatTicketDetailPage';
import CustomerChatWidget from './components/customer/CustomerChatWidget';

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        {/* Back-office routes (staff UI — unchanged) */}
        <Route element={<Layout />}>
          <Route path="/" element={<DashboardPage />} />
          <Route path="/create" element={<CreateCasePage />} />
          <Route path="/cases/:caseId" element={<CaseDetailPage />} />
          <Route path="/chat-tickets" element={<CustomerChatTicketsPage />} />
          <Route path="/chat-tickets/:ticketId" element={<ChatTicketDetailPage />} />
          <Route path="/demo" element={<Navigate to="/create" replace />} />
          <Route path="/safety" element={<SafetyPage />} />
        </Route>
        {/* Customer login — standalone, outside back-office Layout */}
        <Route path="/customer-login" element={<CustomerLoginPage />} />
      </Routes>
      {/* Customer chat widget — floats globally, separate from back-office */}
      <CustomerChatWidget />
    </BrowserRouter>
  );
}
