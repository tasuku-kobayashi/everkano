import { useEffect } from "react";
import clsx from "clsx";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { useUiStore } from "../store/ui";
import { CompareTray } from "./CompareTray";
import { JobBar } from "./JobBar";
import { VramMeter } from "./VramMeter";

const NAV = [
  { to: "/", label: "キャラクター", icon: "👤", end: true },
  { to: "/create", label: "作成", icon: "✨" },
  { to: "/workspace", label: "ワークスペース", icon: "🎨" },
  { to: "/gallery", label: "ギャラリー", icon: "🖼" },
  { to: "/settings", label: "設定", icon: "⚙" },
];

export function AppLayout() {
  const apiKey = useUiStore((s) => s.apiKey);
  const unauthorized = useUiStore((s) => s.unauthorized);
  const blurDefault = useUiStore((s) => s.blurDefault);
  const navigate = useNavigate();
  const location = useLocation();

  // First launch (no key) or a rejected key: send the user to the settings screen
  useEffect(() => {
    if ((!apiKey || unauthorized) && location.pathname !== "/settings") {
      navigate("/settings", { replace: true, state: { reason: !apiKey ? "no-key" : "unauthorized" } });
    }
  }, [apiKey, unauthorized, location.pathname, navigate]);

  return (
    <div className="flex h-full">
      <nav className="flex w-52 shrink-0 flex-col border-r border-ink-700 bg-ink-900" aria-label="メインナビゲーション">
        <div className="px-4 py-3 text-sm font-semibold tracking-wide text-slate-200">
          portrait-studio
          <div className="text-[10px] font-normal text-slate-500">ローカル専用 · 127.0.0.1</div>
        </div>
        <ul className="flex flex-col gap-0.5 px-2">
          {NAV.map((item) => (
            <li key={item.to}>
              <NavLink
                to={item.to}
                end={item.end}
                className={({ isActive }) => clsx("flex items-center gap-2 rounded-md px-3 py-2 text-sm", isActive ? "bg-accent text-white" : "text-slate-300 hover:bg-ink-800")}
              >
                <span aria-hidden>{item.icon}</span>
                {item.label}
              </NavLink>
            </li>
          ))}
        </ul>
        <div className="mt-auto space-y-2 px-4 py-3 text-[11px] text-slate-500">
          <div>
            NSFW ぼかし: <span className={blurDefault ? "text-emerald-300" : "text-amber-300"}>{blurDefault ? "既定 ON" : "OFF"}</span>
          </div>
          <div className="space-x-1">
            <span className="kbd">Enter</span> 生成 <span className="kbd">←→</span> 画像 <span className="kbd">S</span> 保存 <span className="kbd">C</span> 比較 <span className="kbd">Esc</span> 閉じる
          </div>
        </div>
      </nav>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center justify-between gap-4 border-b border-ink-700 bg-ink-900 px-4 py-2">
          <h1 className="text-sm font-medium text-slate-300">{NAV.find((n) => (n.end ? location.pathname === n.to : location.pathname.startsWith(n.to)))?.label ?? ""}</h1>
          {apiKey && <VramMeter />}
        </header>
        <main className="min-h-0 flex-1 overflow-auto">
          <Outlet />
        </main>
        <CompareTray />
        {apiKey && <JobBar />}
      </div>
    </div>
  );
}
