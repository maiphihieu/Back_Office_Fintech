/* ─── Layout with sidebar navigation + language toggle ─── */

import { NavLink, Outlet, useLocation } from 'react-router-dom';
import { useI18n } from '../lib/i18n';

export default function Layout() {
  const { t, toggleLang } = useI18n();
  const location = useLocation();
  const isDashboard = location.pathname === '/create';

  const NAV_ITEMS = [
    { to: '/', icon: '📋', label: t('nav.cases') },
    { to: '/chat-tickets', icon: '💬', label: t('nav.chat_tickets') },
    { to: '/create', icon: '➕', label: t('nav.create') },
    { to: '/safety', icon: '🛡️', label: t('nav.safety') },
  ];

  return (
    <div className="app-layout">
      <aside className="sidebar">
        <div className="sidebar-brand">
          <h1>Fintech Agent</h1>
          <p>{t('nav.brand_subtitle')}</p>
        </div>
        <nav className="sidebar-nav">
          {NAV_ITEMS.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === '/'}
              className={({ isActive }) =>
                `sidebar-link ${isActive ? 'active' : ''}`
              }
            >
              <span className="icon">{item.icon}</span>
              {item.label}
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-footer">
          <button
            className="lang-toggle-btn"
            onClick={toggleLang}
            title="Switch language"
          >
            {t('nav.lang_toggle')}
          </button>
        </div>
      </aside>
      <main className={`main-content${isDashboard ? ' main-content--dashboard' : ''}`}>
        <Outlet />
      </main>
    </div>
  );
}
