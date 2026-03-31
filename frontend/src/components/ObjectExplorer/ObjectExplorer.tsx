import { useState, useEffect } from 'react';
import { AppContext } from '../../App';
import { getDatabases, getTables, getViews, getProcedures, getFunctions } from '../../services/api';
import { Server, TreeNode } from '../../types';
import { VscDatabase, VscServer, VscTable, VscSymbolMethod, VscSymbolMisc, VscFolder, VscEye } from 'react-icons/vsc';
import './ObjectExplorer.css';

interface Props {
  ctx: AppContext;
}

function ObjectExplorer({ ctx }: Props) {
  const [tree, setTree] = useState<TreeNode[]>([]);
  const [expandedNodes, setExpandedNodes] = useState<Set<string>>(new Set());
  const [loadingNodes, setLoadingNodes] = useState<Set<string>>(new Set());
  const [childrenMap, setChildrenMap] = useState<Record<string, TreeNode[]>>({});

  useEffect(() => {
    const serverNodes: TreeNode[] = ctx.servers.map((s) => ({
      id: `server-${s.id}`,
      label: s.name,
      type: 'server' as const,
      data: s,
    }));
    setTree(serverNodes);
  }, [ctx.servers]);

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

    // Expand and load children if needed
    setExpandedNodes((prev) => new Set(prev).add(nodeId));

    if (childrenMap[nodeId]) return; // already loaded

    setLoadingNodes((prev) => new Set(prev).add(nodeId));

    try {
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
      }

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

  const getIcon = (type: string) => {
    switch (type) {
      case 'server': return <VscServer />;
      case 'database': return <VscDatabase />;
      case 'folder': return <VscFolder />;
      case 'table': return <VscTable />;
      case 'view': return <VscEye />;
      case 'procedure': return <VscSymbolMethod />;
      case 'function': return <VscSymbolMisc />;
      default: return null;
    }
  };

  const hasChildren = (node: TreeNode) =>
    node.type === 'server' || node.type === 'database' || node.type === 'folder';

  const renderNode = (node: TreeNode, depth: number = 0) => {
    const isExpanded = expandedNodes.has(node.id);
    const isLoading = loadingNodes.has(node.id);
    const children = childrenMap[node.id] || [];
    const expandable = hasChildren(node);

    return (
      <div key={node.id}>
        <div
          className={`tree-node ${ctx.activeTable && node.type === 'table' &&
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
        >
          <span className="tree-arrow">
            {expandable ? (isExpanded ? '\u25bc' : '\u25b6') : ' '}
          </span>
          <span className="tree-icon">{getIcon(node.type)}</span>
          <span className="tree-label">{node.label}</span>
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
    </div>
  );
}

export default ObjectExplorer;
