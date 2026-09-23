import React from 'react';
import { BrowserRouter, Routes, Route, Link, useLocation } from 'react-router-dom';
import { CrewHUD } from './pages/CrewHUD';
import { GroundOps } from './pages/GroundOps';
import './App.css';

const ViewSwitcher = () => {
  const location = useLocation();
  const isOps = location.pathname === '/ops';

  return (
    <div className="view-switcher">
      <Link to={isOps ? '/' : '/ops'} className="switch-link">
        {isOps ? 'Crew ▸' : 'Ops ▸'}
      </Link>
    </div>
  );
};

const App: React.FC = () => {
  return (
    <BrowserRouter>
      <ViewSwitcher />
      <Routes>
        <Route path="/" element={<CrewHUD />} />
        <Route path="/ops" element={<GroundOps />} />
      </Routes>
    </BrowserRouter>
  );
};

export default App;
