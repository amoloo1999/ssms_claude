import { useState, useEffect, useRef } from 'react';
import { AppContext } from '../../App';
import { getDatabases, getTables, getViews, getProcedures, getFunctions, getTableColumns, getTableIndexes, executeQuery } from '../../services/api';
import { Server, TreeNode, ColumnInfo, IndexInfo } from '../../types';
import { VscDatabase, VscServer, VscTable, VscSymbolMethod, VscSymbolMisc, VscFolder, VscEye, VscSymbolField } from 'react-icons/vsc';
import './ObjectExplorer.css';

interface Props {
  ctx: AppContext;
}

interface ContextMenu {
  x: number;
  y: number;
  node: TreeNode;
}

interface PropertiesModal {
  tableName: string;
  schema: string;
  database: string;
  columns: ColumnInfo[];
  indexes: IndexInfo[];
}

function ObjectExplorer({ ctx }: Props) {
  const [tree, setTree] = useState<TreeNode[]>([]);
  const [expandedNodes, setExpandedNodes] = useState<Set<string>>(new Set());
  const [loadingNodes, setLoadingNodes] = useState<Set<string>>(new Set());
  const [childrenMap, setChildrenMap] = useState<Record<string, TreeNode[]>>({});
  const [contextMenu, setContextMenu] = useState<ContextMenu | null>(null);
  const [renameNode, setRenameNode] = useState<TreeNode | null>(null);
  const [renameValue, setRenameValue] = useState('');
  const [properties, setProperties] = useState<PropertiesModal | null>(null);
  const [schemaModal, setSchemaModal] = useState<{ columns: ColumnInfo[]; tableName: string } | null>(null);
  const renameInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const serverNodes: TreeNode[] = ctx.servers.map((s) => ({
      id: `server-${s.id}`,
      label: s.name,
      type: 'server' as const,
      data: s,
    }));
    setTree(serverNodes);
  }, [ctx.servers]);

  // Close context menu on click outside
  useEffect(() => {
    const handleClick = () => setContextMenu(null);
    document.addEventListener('click', handleClick);
    return () => document.removeEventListener('click', handleClick);
  }, []);

  // Focus rename input
  useEffect(() => {
    if (renameNode && renameInputRef.current) {
      renameInputRef.current.focus();
      renameInputRef.current.select();
    }
  }, [renameNode]);

  // Handle refresh requests from other components
  useEffect(() => {
    if (ctx.refreshExplorerNode) {
      const nodeId = ctx.refreshExplorerNode;
      ctx.setRefreshExplorerNode(null);
      // Find the parent folder node for this table and refresh it
      const parentId = Object.keys(childrenMap).find((key) =>
        childrenMap[key]?.some((child) => child.id === nodeId)
      );
      if (parentId) {
        refreshNode(parentId);
      }
    }
  }, [ctx.refreshExplorerNode]);

  const loadChildren = async (node: TreeNode): Promise<TreeNode[]> => {
    let children: TreeNode[] = [];

    if (node.type === 'server') {
      const serverId = node.data.id;
      const result = await getDatabases(serverId);
      children = (result.databases || []).map((db: string) => ({
        id: `db-${serverId}-${db}`,
        label: db,
        type: 'database' as const,
        data: { serverId, database: db },
      }));
    } else if (node.type === 'database') {
      const { serverId, database } = node.data;
      children = [
        { id: `tables-${serverId}-${database}`, label: 'Tables', type: 'folder' as const, data: { serverId, database, kind: 'tables' } },
        { id: `views-${serverId}-${database}`, label: 'Views', type: 'folder' as const, data: { serverId, database, kind: 'views' } },
        { id: `procs-${serverId}-${database}`, label: 'Stored Procedures', type: 'folder' as const, data: { serverId, database, kind: 'procedures' } },
        { id: `funcs-${serverId}-${database}`, label: 'Functions', type: 'folder' as const, data: { serverId, database, kind: 'functions' } },
      ];
    } else if (node.type === 'folder') {
      const { serverId, database, kind } = node.data;
      if (kind === 'tables') {
        const result = await getTables(serverId, database);
        children = (result.tables || []).map((t: any) => ({
          id: `table-${serverId}-${database}-${t.schema}.${t.name}`,
          label: `${t.schema}.${t.name}`,
          type: 'table' as const,
          data: { serverId, database, schema: t.schema, name: t.name },
        }));
      } else if (kind === 'views') {
        const result = await getViews(serverId, database);
        children = (result.views || []).map((v: any) => ({
          id: `view-${serverId}-${database}-${v.schema}.${v.name}`,
          label: `${v.schema}.${v.name}`,
          type: 'view' as const,
          data: { serverId, database, schema: v.schema, name: v.name },
        }));
      } else if (kind === 'procedures') {
        const result = await getProcedures(serverId, database);
        children = (result.procedures || []).map((p: any) => ({
          id: `proc-${serverId}-${database}-${p.schema}.${p.name}`,
          label: `${p.schema}.${p.name}`,
          type: 'procedure' as const,
          data: { serverId, database, schema: p.schema, name: p.name },
        }));
      } else if (kind === 'functions') {
        const result = await getFunctions(serverId, database);
        children = (result.functions || []).map((f: any) => ({
          id: `func-${serverId}-${database}-${f.schema}.${f.name}`,
          label: `${f.schema}.${f.name}`,
          type: 'function' as const,
          data: { serverId, database, schema: f.schema, name: f.name },
        }));
      }
    } else if (node.type === 'table') {
      const { serverId, database, schema, name } = node.data;
      const result = await getTableColumns(serverId, database, schema, name);
      children = (result.columns || []).map((col: any) => ({
        id: `col-${serverId}-${database}-${schema}.${name}-${col.name}`,
        label: `${col.name} (${col.data_type}${col.max_length ? `(${col.max_length})` : ''}${col.is_nullable ? ', null' : ', not null'}${col.is_primary_key ? ', PK' : ''})`,
        type: 'column' as const,
        data: col,
      }));
    }

    return children;
  };

  const refreshNode = async (nodeId: string) => {
    // Find the node in the tree or children
    const findNode = (nodes: TreeNode[]): TreeNode | null => {
      for (const n of nodes) {
        if (n.id === nodeId) return n;
      }
      for (const key of Object.keys(childrenMap)) {
        for (const n of childrenMap[key]) {
          if (n.id === nodeId) return n;
        }
      }
      return null;
    };

    const node = findNode(tree);
    if (!node) return;

    setLoadingNodes((prev) => new Set(prev).add(nodeId));
    try {
      const children = await loadChildren(node);
      setChildrenMap((prev) => ({ ...prev, [nodeId]: children }));
    } catch (err) {
      console.error('Failed to refresh:', err);
    } finally {
      setLoadingNodes((prev) => {
        const next = new Set(prev);
        next.delete(nodeId);
        return next;
      });
    }
  };

  const toggleNode = async (node: TreeNode) => {
    const nodeId = node.id;

    if (expandedNodes.has(nodeId)) {
      setExpandedNodes((prev) => {
        const next = new Set(prev);
        next.delete(nodeId);
        return next;
      });
      return;
    }

    setExpandedNodes((prev) => new Set(prev).add(nodeId));

    if (childrenMap[nodeId]) return;

    setLoadingNodes((prev) => new Set(prev).add(nodeId));

    try {
      const children = await loadChildren(node);
      setChildrenMap((prev) => ({ ...prev, [nodeId]: children }));
    } catch (err) {
      console.error('Failed to load children:', err);
    } finally {
      setLoadingNodes((prev) => {
        const next = new Set(prev);
        next.delete(nodeId);
        return next;
      });
    }
  };

  const handleNodeClick = (node: TreeNode) => {
    if (node.type === 'table') {
      const { serverId, database, schema, name } = node.data;
      ctx.setActiveQuery({ serverId, database });
      ctx.setActiveTable({ serverId, database, schema, table: name });
      ctx.setActiveTab('table');
    } else if (node.type === 'database') {
      ctx.setActiveQuery({ serverId: node.data.serverId, database: node.data.database });
    }
  };

  const handleContextMenu = (e: React.MouseEvent, node: TreeNode) => {
    if (node.type !== 'table') return;
    e.preventDefault();
    e.stopPropagation();
    setContextMenu({ x: e.clientX, y: e.clientY, node });
  };

  const handleSelectTop1000 = (node: TreeNode) => {
    const { serverId, database, schema, name } = node.data;
    const sql = `SELECT TOP 1000 * FROM [${schema}].[${name}]`;
    ctx.setActiveQuery({ serverId, database });
    ctx.setPendingQuery({ serverId, database, sql });
    ctx.setActiveTab('query');
    setContextMenu(null);
  };

  const handleRenameTable = (node: TreeNode) => {
    setRenameNode(node);
    setRenameValue(node.data.name);
    setContextMenu(null);
  };

  const submitRename = async () => {
    if (!renameNode || !renameValue || renameValue === renameNode.data.name) {
      setRenameNode(null);
      return;
    }

    const { serverId, database, schema, name } = renameNode.data;
    try {
      await executeQuery(serverId, database, `EXEC sp_rename '${schema}.${name}', '${renameValue}'`);
      // Refresh the parent tables folder
      const parentId = `tables-${serverId}-${database}`;
      await refreshNode(parentId);
    } catch (err: any) {
      alert('Rename failed: ' + (err.response?.data?.detail || err.message));
    }
    setRenameNode(null);
  };

  const handleDeleteTable = async (node: TreeNode) => {
    const { serverId, database, schema, name } = node.data;
    setContextMenu(null);

    if (!confirm(`Are you sure you want to delete [${schema}].[${name}]? This cannot be undone.`)) {
      return;
    }

    try {
      await executeQuery(serverId, database, `DROP TABLE [${schema}].[${name}]`);
      const parentId = `tables-${serverId}-${database}`;
      await refreshNode(parentId);
    } catch (err: any) {
      alert('Delete failed: ' + (err.response?.data?.detail || err.message));
    }
  };

  const handleRefreshTable = (node: TreeNode) => {
    const { serverId, database } = node.data;
    const parentId = `tables-${serverId}-${database}`;
    refreshNode(parentId);
    setContextMenu(null);
  };

  const handleTableProperties = async (node: TreeNode) => {
    const { serverId, database, schema, name } = node.data;
    setContextMenu(null);

    try {
      const [colRes, idxRes] = await Promise.all([
        getTableColumns(serverId, database, schema, name),
        getTableIndexes(serverId, database, schema, name),
      ]);
      setProperties({
        tableName: `[${schema}].[${name}]`,
        schema,
        database,
        columns: colRes.columns || [],
        indexes: idxRes.indexes || [],
      });
    } catch (err) {
      console.error('Failed to load properties:', err);
    }
  };

  const handleShowSchema = async (node: TreeNode) => {
    const { serverId, database, schema, name } = node.data;
    setContextMenu(null);

    try {
      const colRes = await getTableColumns(serverId, database, schema, name);
      setSchemaModal({
        columns: colRes.columns || [],
        tableName: `[${schema}].[${name}]`,
      });
    } catch (err) {
      console.error('Failed to load schema:', err);
    }
  };

  const getIcon = (type: string) => {
    switch (type) {
      case 'server': return <VscServer />;
      case 'database': return <VscDatabase />;
      case 'folder': return <VscFolder />;
      case 'table': return <VscTable />;
      case 'view': return <VscEye />;
      case 'procedure': return <VscSymbolMethod />;
      case 'function': return <VscSymbolMisc />;
      case 'column': return <VscSymbolField />;
      default: return null;
    }
  };

  const hasChildren = (node: TreeNode) =>
    node.type === 'server' || node.type === 'database' || node.type === 'folder' || node.type === 'table';

  const renderNode = (node: TreeNode, depth: number = 0) => {
    const isExpanded = expandedNodes.has(node.id);
    const isLoading = loadingNodes.has(node.id);
    const children = childrenMap[node.id] || [];
    const expandable = hasChildren(node);
    const isRenaming = renameNode?.id === node.id;

    return (
      <div key={node.id}>
        <div
          className={`tree-node ${node.type === 'column' ? 'column-node' : ''} ${ctx.activeTable && node.type === 'table' &&
            node.data.serverId === ctx.activeTable.serverId &&
            node.data.database === ctx.activeTable.database &&
            node.data.schema === ctx.activeTable.schema &&
            node.data.name === ctx.activeTable.table
            ? 'active' : ''}`}
          style={{ paddingLeft: depth * 16 + 8 }}
          onClick={() => {
            if (expandable) toggleNode(node);
            handleNodeClick(node);
          }}
          onContextMenu={(e) => handleContextMenu(e, node)}
        >
          <span className="tree-arrow">
            {expandable ? (isExpanded ? '\u25bc' : '\u25b6') : ' '}
          </span>
          <span className="tree-icon">{getIcon(node.type)}</span>
          {isRenaming ? (
            <input
              ref={renameInputRef}
              className="rename-input"
              value={renameValue}
              onChange={(e) => setRenameValue(e.target.value)}
              onBlur={submitRename}
              onKeyDown={(e) => {
                if (e.key === 'Enter') submitRename();
                if (e.key === 'Escape') setRenameNode(null);
              }}
              onClick={(e) => e.stopPropagation()}
            />
          ) : (
            <span className="tree-label">{node.label}</span>
          )}
          {isLoading && <span className="tree-loading">...</span>}
        </div>
        {isExpanded && children.map((child) => renderNode(child, depth + 1))}
      </div>
    );
  };

  return (
    <div className="object-explorer">
      <div className="explorer-header">Object Explorer</div>
      <div className="explorer-tree">
        {tree.length === 0 ? (
          <div className="explorer-empty">
            No servers configured.<br />
            Go to Server Manager to add one.
          </div>
        ) : (
          tree.map((node) => renderNode(node))
        )}
      </div>

      {/* Context Menu */}
      {contextMenu && (
        <div
          className="context-menu"
          style={{ top: contextMenu.y, left: contextMenu.x }}
          onClick={(e) => e.stopPropagation()}
        >
          <div className="context-menu-item" onClick={() => handleSelectTop1000(contextMenu.node)}>
            Select Top 1000 Rows
          </div>
          <div className="context-menu-separator" />
          <div className="context-menu-item" onClick={() => handleRenameTable(contextMenu.node)}>
            Rename Table
          </div>
          <div className="context-menu-item context-menu-danger" onClick={() => handleDeleteTable(contextMenu.node)}>
            Delete Table
          </div>
          <div className="context-menu-separator" />
          <div className="context-menu-item" onClick={() => handleRefreshTable(contextMenu.node)}>
            Refresh
          </div>
          <div className="context-menu-item" onClick={() => handleTableProperties(contextMenu.node)}>
            Table Properties
          </div>
          <div className="context-menu-item" onClick={() => handleShowSchema(contextMenu.node)}>
            Schema
          </div>
        </div>
      )}

      {/* Properties Modal */}
      {properties && (
        <div className="modal-overlay" onClick={() => setProperties(null)}>
          <div className="modal-content" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h3>Table Properties: {properties.tableName}</h3>
              <button className="modal-close" onClick={() => setProperties(null)}>&times;</button>
            </div>
            <div className="modal-body">
              <div className="properties-section">
                <h4>Columns ({properties.columns.length})</h4>
                <table className="properties-table">
                  <thead>
                    <tr>
                      <th>#</th>
                      <th>Name</th>
                      <th>Type</th>
                      <th>Nullable</th>
                      <th>PK</th>
                      <th>Default</th>
                    </tr>
                  </thead>
                  <tbody>
                    {properties.columns.map((col) => (
                      <tr key={col.name}>
                        <td>{col.ordinal_position}</td>
                        <td>{col.name}</td>
                        <td>{col.data_type}{col.max_length ? `(${col.max_length})` : ''}</td>
                        <td>{col.is_nullable ? 'YES' : 'NO'}</td>
                        <td>{col.is_primary_key ? 'PK' : ''}</td>
                        <td>{col.default_value || ''}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {properties.indexes.length > 0 && (
                <div className="properties-section">
                  <h4>Indexes ({properties.indexes.length})</h4>
                  <table className="properties-table">
                    <thead>
                      <tr>
                        <th>Name</th>
                        <th>Type</th>
                        <th>Unique</th>
                        <th>PK</th>
                        <th>Columns</th>
                      </tr>
                    </thead>
                    <tbody>
                      {properties.indexes.map((idx) => (
                        <tr key={idx.name}>
                          <td>{idx.name}</td>
                          <td>{idx.type}</td>
                          <td>{idx.is_unique ? 'YES' : 'NO'}</td>
                          <td>{idx.is_primary_key ? 'YES' : ''}</td>
                          <td>{idx.columns}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Schema Modal */}
      {schemaModal && (
        <div className="modal-overlay" onClick={() => setSchemaModal(null)}>
          <div className="modal-content modal-narrow" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h3>Schema: {schemaModal.tableName}</h3>
              <button className="modal-close" onClick={() => setSchemaModal(null)}>&times;</button>
            </div>
            <div className="modal-body">
              <table className="properties-table">
                <thead>
                  <tr>
                    <th>Column</th>
                    <th>Type</th>
                    <th>Nullable</th>
                    <th>PK</th>
                  </tr>
                </thead>
                <tbody>
                  {schemaModal.columns.map((col) => (
                    <tr key={col.name}>
                      <td>{col.name}</td>
                      <td>{col.data_type}{col.max_length ? `(${col.max_length})` : ''}</td>
                      <td>{col.is_nullable ? 'YES' : 'NO'}</td>
                      <td>{col.is_primary_key ? 'PK' : ''}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default ObjectExplorer;
