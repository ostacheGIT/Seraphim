import { Plus, Settings2, Trash2 } from "lucide-react";
import { Conversation } from "../types";

interface SidebarProps {
  conversations: Conversation[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
  onDelete: (id: string) => void;
}

function groupByDay(convos: Conversation[]) {
  const today = new Date().toDateString();
  const yesterday = new Date(Date.now() - 86400000).toDateString();
  const groups: Record<string, Conversation[]> = {};

  for (const c of convos) {
    const day = new Date(c.updatedAt).toDateString();
    const label =
        day === today ? "Aujourd'hui" : day === yesterday ? "Hier" : day;
    if (!groups[label]) groups[label] = [];
    groups[label].push(c);
  }
  return groups;
}

export default function Sidebar({
                                  conversations,
                                  activeId,
                                  onSelect,
                                  onNew,
                                  onDelete,
                                }: SidebarProps) {
  const groups = groupByDay(conversations);

  return (
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-logo">S</div>
          <div>
            <h1 className="brand-name">Seraphim</h1>
            <p className="brand-sub">local · private · yours</p>
          </div>
        </div>

        <button className="new-chat-btn" onClick={onNew}>
          <Plus size={16} />
          <span>New conversation</span>
        </button>

        <div className="conversation-scroll">
          {Object.entries(groups).map(([label, items]) => (
              <div key={label} className="conversation-group">
                <div className="section-label">{label}</div>
                <div className="conversation-list">
                  {items.map((c) => (
                      <div
                          key={c.id}
                          className={`conversation-item ${activeId === c.id ? "active" : ""}`}
                      >
                        <button
                            className="conversation-title"
                            onClick={() => onSelect(c.id)}
                        >
                          {c.title}
                        </button>
                        <button
                            className="conversation-delete"
                            onClick={(e) => {
                              e.stopPropagation();
                              onDelete(c.id);
                            }}
                            aria-label="Supprimer"
                        >
                          <Trash2 size={13} />
                        </button>
                      </div>
                  ))}
                </div>
              </div>
          ))}
        </div>

        <div className="sidebar-footer">
          <button className="ghost-icon" aria-label="Paramètres">
            <Settings2 size={16} />
          </button>
          <span className="sidebar-version">v0.1.0</span>
        </div>
      </aside>
  );
}