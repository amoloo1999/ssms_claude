import { useState, useEffect, useCallback } from 'react';
import { Routes, Route, Navigate } from 'react-router-dom';
import { getMe } from './services/api';
import { User, Server, TableItem, ColumnInfo } from './types';
import Layout from './components/Layout/Layout';
import LoginPage from './pages/LoginPage';
import './App.css';

export interface ActiveTable {
  serverId: number;
  database: string;
  schema: string;
  table: string;
}

export interface AppContext {
  user: User | null;
  servers: Server[];
  setServers: (s: Server[]) => void;
  activeQuery: { serverId: number; database: string } | null;
  setActiveQuery: (q: { serverId: number; database: string } | null) => void;
  activeTable: ActiveTable | null;
  setActiveTable: (t: ActiveTable | null) => void;
  activeTab: 'query' | 'table' | 'schema';
  setActiveTab: (t: 'query' | 'table' | 'schema') => void;
}

function App() {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [servers, setServers] = useState<Server[]>([]);
  const [activeQuery, setActiveQuery] = useState<{ serverId: number; database: string } | null>(null);
  const [activeTable, setActiveTable] = useState<ActiveTable | null>(null);
  const [activeTab, setActiveTab] = useState<'query' | 'table' | 'schema'>('query');

  useEffect(() => {
    getMe()
      .then(setUser)
      .catch(() => setUser(null))
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return <div className="loading-screen">Loading...</div>;
  }

  const ctx: AppContext = {
    user,
    servers,
    setServers,
    activeQuery,
    setActiveQuery,
    activeTable,
    setActiveTable,
    activeTab,
    setActiveTab,
  };

  return (
    <Routes>
      <Route
        path="/"
        element={user ? <Layout ctx={ctx} /> : <Navigate to="/login" />}
      />
      <Route
        path="/login"
        element={user ? <Navigate to="/" /> : <LoginPage />}
      />
    </Routes>
  );
}

export default App;
