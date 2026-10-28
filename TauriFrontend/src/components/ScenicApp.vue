<script setup lang="ts">
import { computed, reactive, ref } from "vue";
import type { Component } from "vue";
import {
  Activity,
  AlertTriangle,
  Camera,
  Film,
  FolderPlus,
  History,
  KeyRound,
  LogIn,
  LogOut,
  Map,
  LayoutDashboard,
  Sparkles,
  RefreshCw,
  Route,
  Search,
  Settings2,
  UserPlus,
  Users,
} from "@lucide/vue";
import { normalizeAssetUrl } from "../lib/api/http";
import { useWorkbenchStore } from "../stores/workbench";
import MediaPreviewDialog from "./MediaPreviewDialog.vue";
import LiveCameraPreview from "./LiveCameraPreview.vue";
import ScenicAccountDialog from "./scenic/ScenicAccountDialog.vue";
import ScenicAuditView from "./scenic/ScenicAuditView.vue";
import ScenicVisitorServiceView from "./scenic/ScenicVisitorServiceView.vue";
import ScenicHistoryView from "./scenic/ScenicHistoryView.vue";
import ScenicMatchingView from "./scenic/ScenicMatchingView.vue";
import ScenicMonitorView from "./scenic/ScenicMonitorView.vue";
import ScenicProjectView from "./scenic/ScenicProjectView.vue";
import ScenicRegistrationView from "./scenic/ScenicRegistrationView.vue";
import ScenicVlogView from "./scenic/ScenicVlogView.vue";
import { progressPercent, useScenicLibraryStatus } from "./scenic/useScenicLibraryStatus";

type ScenicTab = "overview" | "registration" | "monitor" | "matching" | "vlog" | "history" | "audit" | "visitor";

const emit = defineEmits<{
  (event: "switch-core"): void;
}>();

const store = useWorkbenchStore();
const activeTab = ref<ScenicTab>("registration");
const scenicRegisterMode = ref(false);
const showProjectModal = ref(false);
const showAccountModal = ref(false);
const overviewCamera = computed(() =>
  store.scenicCameraWall.find(camera => camera.monitor_running || camera.running)
  || store.scenicCameraWall.find(camera => camera.latest_frame_url));
const overviewFrameUrl = computed(() => {
  const camera = overviewCamera.value;
  if (!camera?.latest_frame_url) return "";
  const url = normalizeAssetUrl(camera.latest_frame_url);
  return `${url}${url.includes("?") ? "&" : "?"}v=${camera.latest_frame_version || 0}`;
});

const scenicTabs: Array<{ key: ScenicTab; label: string; icon: Component }> = [
  { key: "overview", label: "运营总览", icon: LayoutDashboard },
  { key: "registration", label: "游客登记", icon: Users },
  { key: "monitor", label: "园内摄像头", icon: Camera },
  { key: "matching", label: "游客游玩记录匹配", icon: Search },
  { key: "vlog", label: "生成视频短片", icon: Film },
  { key: "history", label: "游客历史", icon: History },
  { key: "audit", label: "审计", icon: Activity },
  { key: "visitor", label: "游客自助服务", icon: Route },
];

const mediaPreview = reactive({
  open: false,
  title: "",
  type: "image" as "image" | "video",
  source: "",
  frameSource: "",
  downloadSource: "",
  fallbackSource: "",
  downloadName: "",
  seekSeconds: 0,
  fallbackSeekSeconds: 0,
  error: "",
});

const loginForm = computed(() => store.scenicLogin);
const { libraryProgress, libraryStatusText } = useScenicLibraryStatus();
const pageTitle = computed(() => {
  if (activeTab.value === "overview") return "让每一段旅程，都有迹可循";
  if (activeTab.value === "registration") return "游客登记";
  if (activeTab.value === "monitor") return "园内摄像头";
  if (activeTab.value === "matching") return "游客游玩记录 · 匹配复核";
  if (activeTab.value === "vlog") return "短片工作室";
  if (activeTab.value === "history") return "游客历史";
  if (activeTab.value === "visitor") return "游客自助服务";
  return "审计";
});
const pageSummary = computed(() => {
  return [
    `今日 ${store.scenicDashboard?.today_sessions || 0}`,
    `匹配 ${store.scenicDashboard?.matched_sessions || 0}`,
    `短片 ${store.scenicDashboard?.ready_vlogs || 0}`,
    `收入 ${money(store.scenicDashboard?.paid_revenue_cents || 0)}`,
  ].join(" · ");
});
function money(cents?: number): string {
  return `¥${((cents || 0) / 100).toFixed(0)}`;
}

function fileNameFromUrl(value?: string, fallback = "myreid_media"): string {
  const clean = String(value || "").split("#")[0].split("?")[0];
  const name = clean.split(/[\\/]/).pop() || fallback;
  try {
    return decodeURIComponent(name).replace(/[\\/:*?"<>|]/g, "_") || fallback;
  } catch {
    return name.replace(/[\\/:*?"<>|]/g, "_") || fallback;
  }
}

function isVideoSource(mediaType?: string, source?: string): boolean {
  const text = `${mediaType || ""} ${source || ""}`.toLowerCase();
  return text.includes("video") || text.includes("camera") || /\.(mp4|mov|avi|mkv|webm)(\?|#|$)/i.test(text);
}

function previewAssetUrl(value?: string | null): string {
  return normalizeAssetUrl(value || "");
}

async function submitLogin(): Promise<void> {
  if (store.busy) return;
  await store.login("scenic");
  if (!store.error) showAccountModal.value = false;
}

async function submitRegister(): Promise<void> {
  if (store.busy) return;
  await store.register("scenic");
  if (!store.error) scenicRegisterMode.value = false;
}

async function switchTab(tab: ScenicTab): Promise<void> {
  activeTab.value = tab;
  if (tab === "history") {
    await store.loadSearchHistory("scenic");
  } else if (tab === "audit") {
    await store.loadAuditLogs("scenic");
  } else if (tab === "monitor") {
    await store.refreshWorkspace("scenic");
  } else if ((tab === "matching" || tab === "vlog") && store.selectedTourist) {
    await store.selectTourist(store.selectedTourist.id);
  }
}

async function selectTourist(sessionId: number, nextTab?: ScenicTab): Promise<void> {
  await store.selectTourist(sessionId);
  if (nextTab) activeTab.value = nextTab;
}

function openMediaPreview(payload: {
  title: string;
  source?: string;
  mediaType?: string;
  frameSource?: string;
  fallbackSource?: string;
  seekSeconds?: number;
  fallbackSeekSeconds?: number;
  downloadSource?: string;
  downloadName?: string;
}): void {
  const source = previewAssetUrl(payload.source || "");
  if (!source) return;
  mediaPreview.title = payload.title;
  mediaPreview.source = source;
  mediaPreview.frameSource = previewAssetUrl(payload.frameSource || "");
  mediaPreview.downloadSource = previewAssetUrl(payload.downloadSource || payload.source || "");
  mediaPreview.fallbackSource = previewAssetUrl(payload.fallbackSource || payload.downloadSource || payload.source || "");
  mediaPreview.downloadName = payload.downloadName || fileNameFromUrl(payload.downloadSource || payload.source, "scenic_media");
  mediaPreview.seekSeconds = Math.max(0, Number(payload.seekSeconds || 0));
  mediaPreview.fallbackSeekSeconds = Math.max(0, Number(payload.fallbackSeekSeconds ?? payload.seekSeconds ?? 0));
  mediaPreview.error = "";
  mediaPreview.type = isVideoSource(payload.mediaType, source) ? "video" : "image";
  mediaPreview.open = true;
}

</script>

<template>
  <main class="scenic-shell">
    <div class="ambient-grid" aria-hidden="true"></div>
    <div class="scanline" aria-hidden="true"></div>
    <aside class="scenic-sidebar app-sidebar">
      <button class="app-brand" type="button" aria-label="打开景区运营首页" @click="switchTab('overview')"><span class="brand-symbol"><Sparkles :size="21" /></span><span><strong>MyReID</strong><small>VISION WORKSPACE</small></span></button>
      <button class="workspace-switch" type="button" title="返回基础系统" aria-label="返回基础系统" @click="emit('switch-core')">景区运营工作台 <Route :size="14" /></button>
      <p class="nav-caption">运营工作区</p>
      <nav class="scenic-nav rail-nav" aria-label="景区导航">
        <button v-for="tab in scenicTabs.filter(item => item.key !== 'audit')" :key="tab.key" class="rail-button" :class="{ active: activeTab === tab.key }" :aria-current="activeTab === tab.key ? 'page' : undefined" type="button" :title="tab.label" :aria-label="`打开${tab.label}`" @click="switchTab(tab.key)">
          <component :is="tab.icon" :size="17" /><span>{{ tab.key === 'matching' ? '匹配复核' : tab.key === 'vlog' ? '短片工作室' : tab.label }}</span>
        </button>
      </nav>
      <p class="nav-caption">管理</p>
      <nav class="management-nav" aria-label="景区管理">
        <button class="rail-button" type="button" title="项目管理" aria-label="打开景区项目管理" @click="showProjectModal = true"><FolderPlus :size="17" /><span>项目管理</span></button>
        <button class="rail-button" type="button" aria-label="景区账号与权限" @click="showAccountModal = true"><Settings2 :size="17" /><span>账号与权限</span></button>
        <button class="rail-button" :class="{ active: activeTab === 'audit' }" type="button" title="审计" aria-label="打开审计" @click="switchTab('audit')"><Activity :size="17" /><span>审计日志</span></button>
      </nav>
      <div class="sidebar-footer">
        <button class="system-health" type="button" title="刷新景区工作区" aria-label="刷新景区工作区" @click="store.refreshWorkspace('scenic')"><span><i :class="{ online: store.systemReady }"></i>{{ store.systemReady ? '服务运行正常' : '服务状态待确认' }}</span><small>{{ libraryStatusText }} · {{ progressPercent(libraryProgress) }}</small><small>景区工作台 · v1.0</small></button>
        <div class="sidebar-account"><button type="button" :aria-label="store.isAuthenticated ? `打开个人信息：${store.scenicUser?.username || '当前用户'}` : '打开景区登录窗口'" @click="showAccountModal = true"><Users :size="18" /><span>{{ store.scenicUser?.username || '登录景区系统' }}<small>运营工作台</small></span></button><button type="button" title="退出景区账号" aria-label="退出景区账号" :disabled="!store.isAuthenticated" @click="store.logout('scenic')"><LogOut :size="15" /></button></div>
      </div>
    </aside>

    <section class="scenic-main">
      <p v-if="store.activeProject && !store.modelAvailable" role="alert">{{ store.activeProject.reid_model?.unavailable_reason || "项目模型待核验，暂停推理操作" }}</p>
      <div class="workspace-topbar"><button type="button" @click="showProjectModal = true"><FolderPlus :size="16" />{{ store.scenicProjects.find(project => project.id === store.scenicProjectId)?.name || '选择景区项目' }} / {{ store.activeProject?.reid_model?.name || '模型待核验' }}</button><div><span class="workspace-tag">运营工作台</span><button type="button" @click="emit('switch-core')">切换工作台</button></div></div>
      <header class="scenic-header">
        <div>
          <h2>{{ pageTitle }}</h2>
          <p>{{ activeTab === "overview" ? "从入园登记到影片交付，今天的旅程在这里一目了然。" : pageSummary }}</p>
        </div>
        <div class="header-actions">
          <button v-if="activeTab === 'overview'" class="scenic-button primary" type="button" @click="switchTab('registration')"><UserPlus :size="15" />新建登记</button>
          <button class="scenic-button subtle" type="button" @click="showAccountModal = true">
            <UserPlus :size="15" />
            <span>{{ store.isAuthenticated ? (store.scenicUser?.username || "个人信息") : "登录" }}</span>
          </button>
          <button class="scenic-button subtle" type="button" @click="store.refreshWorkspace('scenic')">
            <RefreshCw :size="15" :class="{ spin: store.busy }" />
            <span>刷新</span>
          </button>
        </div>
      </header>

      <div v-if="store.error" class="notice danger">
        <AlertTriangle :size="16" />
        <span>{{ store.error }}</span>
      </div>

      <section v-if="!store.scenicProjectId && store.isAuthenticated" class="empty-state large">
        <Map :size="28" />
        <h3>还没有景区项目</h3>
        <p>创建项目后，游客登记、摄像头、匹配历史和审计都会写入景区独立数据域。</p>
        <button class="scenic-button primary" type="button" @click="showProjectModal = true">
          <FolderPlus :size="16" />
          <span>创建景区项目</span>
        </button>
      </section>

      <div v-else class="scenic-content" :data-page="activeTab">
        <section v-if="activeTab === 'overview'" class="overview-layout" aria-label="运营总览">
          <div class="overview-metrics">
            <article class="panel"><span>今日入园</span><strong>{{ store.scenicDashboard?.today_sessions ?? 0 }}</strong><small>游客登记总数</small></article>
            <article class="panel"><span>已匹配游客</span><strong>{{ store.scenicDashboard?.matched_sessions ?? 0 }}</strong><small>已完成游玩记录匹配</small></article>
            <article class="panel"><span>已完成短片</span><strong>{{ store.scenicDashboard?.ready_vlogs ?? 0 }}</strong><small>珍藏每段游览时光</small></article>
            <article class="panel"><span>已支付收入</span><strong>{{ money(store.scenicDashboard?.paid_revenue_cents) }}</strong><small>已支付订单汇总</small></article>
          </div>
          <article class="panel overview-progress"><div class="panel-head"><h3>今日处理进度</h3><button class="link-button" type="button" @click="switchTab('history')">查看全部 →</button></div><p class="surface-caption">从登记到交付，衔接每一步</p>
            <div class="overview-steps"><button v-for="(step, index) in [{ key: 'registration', label: '游客登记' }, { key: 'matching', label: '匹配复核' }, { key: 'vlog', label: '短片制作' }, { key: 'visitor', label: '订单交付' }] as const" :key="step.key" type="button" @click="switchTab(step.key)"><small>0{{ index + 1 }}</small>{{ step.label }}</button></div>
            <div class="overview-task"><span>待复核游玩片段</span><button type="button" @click="switchTab('matching')">{{ store.touristQueueCounts?.needs_review ?? 0 }} 位游客 · 前往处理 →</button></div>
            <div class="overview-task"><span>正在生成短片</span><button type="button" @click="switchTab('vlog')">{{ store.scenicDashboard?.pending_vlogs ?? 0 }} 个任务 · 前往处理 →</button></div>
          </article>
          <article class="panel overview-camera"><div class="panel-head"><h3>园内摄像头</h3><button class="link-button" type="button" @click="switchTab('monitor')">查看点位 →</button></div><div class="overview-camera-frame"><LiveCameraPreview v-if="overviewCamera" :key="`${store.scenicProjectId}:${overviewCamera.point_id}`" :src="overviewFrameUrl" :stream-endpoint="overviewCamera.point_id ? `/api/v1/scenic/projects/${store.scenicProjectId}/camera-points/${overviewCamera.point_id}/preview-stream` : undefined" auth-scope="scenic" alt="园内相机实时画面" /><span v-else><Camera :size="28" />暂无实时画面</span></div><p class="surface-caption">{{ store.scenicCameraWall.filter(camera => camera.monitor_running || camera.running).length }} 路在线 · 共 {{ store.scenicCameraWall.length }} 路摄像头</p></article>
          <article class="panel overview-recent"><div class="panel-head"><h3>最近游客</h3><button class="link-button" type="button" @click="switchTab('history')">游客历史 →</button></div><div class="table-scroll"><table><thead><tr><th>游客 / 门票号</th><th>登记时间</th><th>匹配片段</th><th>操作</th></tr></thead><tbody><tr v-for="tourist in (store.scenicDashboard?.recent_sessions || store.tourists).slice(0, 5)" :key="tourist.id"><td>{{ tourist.visitor_label || '游客' }} · {{ tourist.ticket_code }}</td><td>{{ tourist.created_at || '—' }}</td><td>{{ tourist.segment_count ?? 0 }} 个片段</td><td><button class="link-button" type="button" @click="selectTourist(tourist.id, 'matching')">查看记录 →</button></td></tr></tbody></table></div><p v-if="!(store.scenicDashboard?.recent_sessions || store.tourists).length" class="empty-inline">暂无登记记录</p></article>
        </section>
        <ScenicRegistrationView
          v-else-if="activeTab === 'registration'"
          @select-tourist="selectTourist($event, 'matching')"
        />

        <ScenicMonitorView
          v-else-if="activeTab === 'monitor'"
          @preview="openMediaPreview"
        />

        <ScenicMatchingView
          v-else-if="activeTab === 'matching'"
          @navigate-vlog="activeTab = 'vlog'"
          @preview="openMediaPreview"
        />

        <ScenicVlogView
          v-else-if="activeTab === 'vlog'"
          @preview="openMediaPreview"
        />

        <ScenicHistoryView
          v-else-if="activeTab === 'history'"
          @select-tourist="selectTourist($event, 'matching')"
          @preview="openMediaPreview"
        />

        <ScenicVisitorServiceView v-else-if="activeTab === 'visitor'" @select-tourist="selectTourist($event, 'matching')" />
        <ScenicAuditView v-else />
      </div>
    </section>

    <div v-if="!store.isAuthenticated" class="login-cover">
      <form class="login-card" @submit.prevent="scenicRegisterMode ? submitRegister() : submitLogin()">
        <h2>景区运营平台</h2>
        <p>{{ scenicRegisterMode ? "创建景区独立账号" : "登录景区独立账号" }}</p>
        <template v-if="!scenicRegisterMode">
          <input v-model="loginForm.account" class="scenic-field" placeholder="用户名或邮箱" autocomplete="username" />
          <input v-model="loginForm.password" class="scenic-field" placeholder="密码" type="password" autocomplete="current-password" />
          <button class="scenic-button primary full-width" type="submit" :disabled="store.busy">
            <LogIn :size="16" />
            <span>登录景区后台</span>
          </button>
        </template>
        <template v-else>
          <input v-model="loginForm.username" class="scenic-field" placeholder="用户名" />
          <input v-model="loginForm.email" class="scenic-field" placeholder="邮箱" />
          <input v-model="loginForm.code" class="scenic-field" placeholder="验证码" />
          <input v-model="loginForm.registerPassword" class="scenic-field" placeholder="密码" type="password" />
          <div class="button-row">
            <button class="scenic-button subtle" type="button" :disabled="store.busy" @click="store.requestEmailCode('scenic', 'register')">
              <KeyRound :size="15" />
              <span>获取验证码</span>
            </button>
            <button class="scenic-button primary" type="submit" :disabled="store.busy">
              <UserPlus :size="15" />
              <span>注册</span>
            </button>
          </div>
          <p v-if="loginForm.debugCode" class="debug-code">验证码 {{ loginForm.debugCode }}</p>
        </template>
        <p v-if="!store.error && store.message" role="status" aria-live="polite">{{ store.message }}</p>
        <div v-if="store.error" class="notice danger">
          <AlertTriangle :size="15" />
          <span>{{ store.error }}</span>
        </div>
        <button class="link-button" type="button" @click="scenicRegisterMode = !scenicRegisterMode">
          {{ scenicRegisterMode ? "已有账号，去登录" : "注册景区账号" }}
        </button>
        <button class="link-button muted" type="button" @click="emit('switch-core')">返回基础系统</button>
      </form>
    </div>

    <ScenicAccountDialog :open="showAccountModal" @close="showAccountModal = false" />
    <ScenicProjectView :open="showProjectModal" @close="showProjectModal = false" />

    <MediaPreviewDialog :preview="mediaPreview" variant="light" @close="mediaPreview.open = false" />
  </main>
</template>

<style scoped>
.scenic-shell {
  display: flex;
  width: 100vw;
  height: 100vh;
  min-width: 1120px;
  overflow: hidden;
  background: #eef2f6;
  color: #0f172a;
}

.scenic-sidebar {
  display: flex;
  width: 248px;
  flex: 0 0 248px;
  flex-direction: column;
  gap: 14px;
  background: #0f172a;
  padding: 18px;
  color: #f8fafc;
}

.status-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 800;
}

.scenic-nav {
  display: grid;
  gap: 6px;
}

.nav-item.active {
  border-color: #28445b;
  background: #172235;
  color: #ffffff;
}

.panel-head,
.button-row,
.header-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}

.scenic-main {
  display: flex;
  min-width: 0;
  flex: 1;
  flex-direction: column;
}

.scenic-header {
  display: flex;
  min-height: 76px;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  border-bottom: 1px solid #dbe4ee;
  background: #ffffff;
  padding: 0 24px;
}

.scenic-header h2 {
  margin: 0;
  font-size: 22px;
  line-height: 1.2;
}

.scenic-header p {
  margin: 4px 0 0;
  color: #5f6f7f;
  font-size: 12px;
}

.scenic-content {
  min-height: 0;
  flex: 1;
  overflow: auto;
  padding: 20px;
}

.notice {
  display: flex;
  align-items: center;
  gap: 8px;
  border-bottom: 1px solid #fecaca;
  background: #fff1f2;
  padding: 10px 24px;
  color: #b91c1c;
  font-size: 13px;
}

.panel-head {
  justify-content: space-between;
  margin-bottom: 12px;
}

.panel-head h3 {
  color: #0f172a;
  font-size: 16px;
  font-weight: 800;
}

.button-row {
  margin-top: 12px;
}

.tourist-row.selected {
  border-color: #0f9f6e;
  background: #eaf7f0;
}

.status-badge {
  min-width: 56px;
  min-height: 24px;
  padding: 0 8px;
}

.scenic-field {
  width: 100%;
  min-height: 38px;
  border: 1px solid #dbe4ee;
  border-radius: 6px;
  background: #ffffff;
  padding: 8px 10px;
  color: #0f172a;
  outline: none;
}

.scenic-field:focus {
  border-color: #0f9f6e;
}

.scenic-field:disabled {
  background: #f1f5f9;
  color: #94a3b8;
}

.scenic-button,
.icon-square {
  display: inline-flex;
  min-height: 36px;
  align-items: center;
  justify-content: center;
  gap: 7px;
  border: 1px solid #cbd5e1;
  border-radius: 6px;
  padding: 8px 12px;
  font-size: 13px;
  font-weight: 800;
  text-decoration: none;
}

.scenic-button.primary {
  border-color: #0f9f6e;
  background: #0f9f6e;
  color: #ffffff;
}

.scenic-button.subtle {
  background: #ffffff;
  color: #0f172a;
}

.scenic-button.compact {
  min-height: 30px;
  padding: 5px 9px;
  font-size: 12px;
}

.scenic-button.full-width {
  width: 100%;
}

.scenic-button.disabled,
.scenic-button:disabled,
.icon-square:disabled {
  cursor: not-allowed;
  opacity: 0.45;
  pointer-events: none;
}

.icon-square {
  width: 38px;
  padding: 0;
  background: #ffffff;
  color: #0f172a;
}

.icon-square.light {
  background: #f8fafc;
}

.tab-strip button.active {
  border-color: #cbd5e1;
  background: #f8fafc;
  color: #0f172a;
}

.section-rule {
  height: 1px;
  margin: 14px 0;
  background: #e2e8f0;
}

.segment-card-actions .scenic-button {
  flex: 1;
  min-width: 0;
}

.vlog-meta .button-row {
  margin-top: auto;
  flex-wrap: wrap;
}

.order-controls .scenic-field {
  max-width: 160px;
}

.empty-state,
.empty-inline {
  display: grid;
  place-items: center;
  color: #64748b;
  text-align: center;
}

.empty-state {
  min-height: 120px;
  gap: 8px;
}

.empty-state.large {
  min-height: 260px;
  border: 1px dashed #cbd5e1;
  border-radius: 8px;
  background: #f8fafc;
  padding: 24px;
}

.empty-state h3,
.empty-state p {
  margin: 0;
}

.empty-state h3 {
  color: #0f172a;
  font-size: 18px;
}

.empty-state p {
  max-width: 460px;
  color: #64748b;
  font-size: 13px;
  line-height: 1.45;
}

.empty-inline {
  min-height: 70px;
  border: 1px dashed #dbe4ee;
  border-radius: 6px;
  background: #f8fafc;
  font-size: 13px;
}

.login-cover,
.modal-layer {
  position: fixed;
  inset: 0;
  z-index: 60;
  display: grid;
  place-items: center;
  background: rgb(244 247 251 / 0.94);
}

.modal-layer {
  background: rgb(15 23 42 / 0.42);
}

.login-card,
.dialog-card {
  display: grid;
  width: 420px;
  gap: 12px;
  border: 1px solid #dbe4ee;
  border-radius: 10px;
  background: #ffffff;
  padding: 24px;
  box-shadow: 0 24px 72px rgb(15 23 42 / 0.18);
}

.login-card h2,
.login-card p {
  margin: 0;
  text-align: center;
}

.login-card h2 {
  font-size: 28px;
}

.login-card p {
  color: #64748b;
  font-size: 13px;
}

.debug-code {
  border: 1px solid #fde68a;
  border-radius: 6px;
  background: #fffbeb;
  padding: 8px;
  color: #b7791f;
}

.link-button {
  border: 0;
  background: transparent;
  color: #0f8a5f;
  font-weight: 800;
}

.link-button.muted {
  color: #64748b;
}

.dialog-card {
  width: min(92vw, 520px);
}

.project-list {
  display: grid;
  max-height: 240px;
  gap: 8px;
  overflow: auto;
}

.project-row-light {
  display: grid;
  gap: 4px;
  border: 1px solid #e2e8f0;
  border-radius: 6px;
  background: #f8fafc;
  padding: 10px;
  color: #0f172a;
  text-align: left;
}

.project-row-light.selected {
  border-color: #0f9f6e;
  background: #eaf7f0;
}

.project-row-light strong,
.project-row-light span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.project-row-light span {
  color: #64748b;
  font-size: 12px;
}

.modal-eyebrow {
  margin: 0 0 2px;
  color: #0f8a5f;
  font-size: 10px;
  font-weight: 800;
  text-transform: uppercase;
}

.spin {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

@media (max-width: 1260px) {
  .scenic-shell {
    min-width: 980px;
  }
}

.scenic-shell {
  background: #070809;
  color: #e4e4e7;
}

.scenic-sidebar,
.scenic-main {
  position: relative;
  z-index: 1;
}

.scenic-sidebar {
  border-right: 1px solid rgb(255 255 255 / 0.07);
  background: rgb(9 9 11 / 0.82);
  box-shadow: 12px 0 42px rgb(0 0 0 / 0.22);
  backdrop-filter: blur(18px);
}

.panel-head h3,
.empty-state h3,
.login-card h2 {
  color: #fafafa;
}

.scenic-header p,
.empty-state p,
.project-row-light span,
.modal-eyebrow {
  color: #a1a1aa;
}

.login-card,
.dialog-card {
  border-color: rgb(255 255 255 / 0.07);
  background: rgb(24 24 27 / 0.46);
  box-shadow: 0 10px 40px rgb(0 0 0 / 0.26);
  backdrop-filter: blur(14px);
}

.project-row-light:hover {
  border-color: rgb(94 234 212 / 0.35);
  background: rgb(39 39 42 / 0.62);
}

.nav-item.active {
  border-color: rgb(94 234 212 / 0.38);
  background: linear-gradient(135deg, rgb(20 184 166 / 0.2), rgb(244 63 94 / 0.11));
  color: #ffffff;
}

.nav-item.active span {
  background: rgb(20 184 166 / 0.22);
  color: #ccfbf1;
}

.scenic-header {
  border-bottom-color: rgb(255 255 255 / 0.07);
  background: rgb(10 11 12 / 0.74);
  backdrop-filter: blur(16px);
}

.scenic-header h2 {
  color: #ffffff;
}

.scenic-content {
  background: transparent;
}

.notice {
  border-bottom-color: rgb(248 113 113 / 0.22);
  background: rgb(127 29 29 / 0.3);
  color: #fecaca;
}

.project-row-light,
.empty-inline,
.empty-state.large {
  border-color: rgb(255 255 255 / 0.07);
  background: rgb(9 9 11 / 0.38);
}

.tourist-row.selected,
.project-row-light.selected {
  border-color: rgb(45 212 191 / 0.55);
  background: rgb(20 184 166 / 0.13);
}

.empty-inline {
  border-color: rgb(255 255 255 / 0.09);
  background: rgb(0 0 0 / 0.32);
  color: #71717a;
}

.link-button,
.modal-eyebrow {
  color: #5eead4;
}

.scenic-field {
  border-color: rgb(255 255 255 / 0.12);
  background: rgb(0 0 0 / 0.24);
  color: #ffffff;
}

.scenic-field:focus {
  border-color: rgb(94 234 212 / 0.58);
  background: rgb(20 184 166 / 0.08);
}

.scenic-field:disabled {
  background: rgb(255 255 255 / 0.06);
  color: #71717a;
}

.scenic-field::placeholder {
  color: #71717a;
}

.scenic-button,
.icon-square {
  border-color: rgb(255 255 255 / 0.12);
  border-radius: 8px;
  transition:
    transform 180ms ease,
    border-color 180ms ease,
    background 180ms ease;
}

.scenic-button:hover,
.icon-square:hover {
  transform: translateY(-1px);
}

.scenic-button.primary {
  border-color: rgb(45 212 191 / 0.42);
  background: linear-gradient(135deg, rgb(20 184 166 / 0.95), rgb(99 102 241 / 0.82));
  color: #ffffff;
}

.scenic-button.subtle,
.icon-square,
.icon-square.light {
  background: rgb(255 255 255 / 0.06);
  color: #e4e4e7;
}

.scenic-button.subtle:hover,
.icon-square:hover,
.icon-square.light:hover {
  border-color: rgb(94 234 212 / 0.45);
  background: rgb(20 184 166 / 0.16);
}

.tab-strip button.active {
  border-color: rgb(94 234 212 / 0.35);
  background: rgb(20 184 166 / 0.16);
  color: #ffffff;
}

.section-rule {
  background: rgb(255 255 255 / 0.08);
}

.login-cover,
.modal-layer {
  background: rgb(0 0 0 / 0.66);
  backdrop-filter: blur(6px);
}

.login-cover {
  left: 72px;
}

.dialog-card,
.login-card {
  background: rgb(24 24 27 / 0.78);
  box-shadow: 0 24px 80px rgb(0 0 0 / 0.52);
}

.debug-code {
  border-color: rgb(251 191 36 / 0.28);
  background: rgb(180 83 9 / 0.16);
  color: #fde68a;
}

.link-button.muted {
  color: #a1a1aa;
}

.scenic-sidebar {
  width: 72px;
  flex: 0 0 72px;
  align-items: center;
  gap: 0;
  padding: 24px 0;
}

.rail-brand,
.rail-button {
  position: relative;
  display: inline-flex;
  width: 42px;
  height: 42px;
  align-items: center;
  justify-content: center;
  border: 1px solid transparent;
  border-radius: 8px;
  background: transparent;
  color: #a1a1aa;
  transition:
    transform 180ms ease,
    border-color 180ms ease,
    background 180ms ease,
    color 180ms ease;
}

.rail-brand {
  border-color: rgb(255 255 255 / 0.14);
  background: rgb(255 255 255 / 0.075);
  color: #c2fff4;
}

.rail-button:hover {
  transform: translateY(-1px);
  border-color: rgb(94 234 212 / 0.45);
  background: rgb(255 255 255 / 0.06);
  color: #ffffff;
}

.rail-button.active {
  border-color: rgb(94 234 212 / 0.35);
  background: rgb(20 184 166 / 0.16);
  color: #67e8f9;
}

.rail-button:disabled {
  cursor: not-allowed;
  opacity: 0.4;
}

.rail-nav {
  display: flex;
  flex: 1;
  flex-direction: column;
  align-items: center;
  gap: 10px;
  margin-top: 30px;
  width: 100%;
}

.rail-bottom {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
  width: 100%;
}

.rail-dot {
  position: absolute;
  right: 7px;
  bottom: 7px;
  width: 6px;
  height: 6px;
  border-radius: 999px;
  background: #f59e0b;
  box-shadow: 0 0 0 2px #09090b;
}

.rail-dot.ok {
  background: #22c55e;
}

.rail-dot.warn {
  background: #f59e0b;
}

.rail-meter {
  display: grid;
  width: 42px;
  gap: 6px;
  justify-items: center;
  border: 1px solid rgb(255 255 255 / 0.07);
  border-radius: 8px;
  background: rgb(255 255 255 / 0.045);
  padding: 8px 0;
}

.rail-meter span {
  color: #5eead4;
  font-size: 10px;
  font-weight: 800;
}

.rail-meter div {
  position: relative;
  width: 5px;
  height: 34px;
  overflow: hidden;
  border-radius: 999px;
  background: rgb(255 255 255 / 0.12);
}

.rail-meter i {
  position: absolute;
  right: 0;
  bottom: 0;
  left: 0;
  display: block;
  border-radius: inherit;
  background: linear-gradient(180deg, #22d3ee, #6366f1);
}

.scenic-header {
  min-height: 72px;
}

.scenic-shell {
  min-width: 960px;
}

.account-dialog {
  width: min(520px, calc(100vw - 40px));
}

.profile-summary {
  display: flex;
  align-items: center;
  gap: 14px;
  border: 1px solid rgb(255 255 255 / 0.07);
  border-radius: 10px;
  background: rgb(9 9 11 / 0.42);
  padding: 14px;
}

.profile-avatar {
  display: grid;
  width: 48px;
  height: 48px;
  flex: 0 0 48px;
  place-items: center;
  border-radius: 50%;
  background: linear-gradient(135deg, #0f766e, #2563eb);
  color: #fff;
  font-size: 18px;
  font-weight: 800;
}

.profile-summary > div:nth-child(2) {
  display: grid;
  min-width: 0;
  flex: 1;
  gap: 3px;
}

.profile-summary strong {
  color: #fafafa;
}

.profile-summary span {
  color: #a1a1aa;
  font-size: 12px;
}

.profile-rows {
  display: grid;
  gap: 8px;
}

.profile-rows > div {
  display: flex;
  justify-content: space-between;
  gap: 20px;
  color: #a1a1aa;
  font-size: 12px;
}

.profile-rows strong {
  overflow: hidden;
  color: #e4e4e7;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.settings-block {
  display: grid;
  gap: 8px;
}

.settings-block h4 {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0 0 4px;
  color: #fafafa;
  font-size: 14px;
}

.setting-row {
  display: flex;
  min-height: 52px;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  border-radius: 9px;
  padding: 8px 10px;
  background: rgb(9 9 11 / 0.34);
}

.setting-row > span {
  display: grid;
  gap: 3px;
}

.setting-row strong {
  color: #e4e4e7;
  font-size: 12px;
}

.setting-row small {
  color: #71717a;
  font-size: 10px;
}

.setting-select {
  width: 108px;
  min-width: 108px;
  padding: 7px 9px;
}

.scenic-button.danger {
  border-color: rgb(244 63 94 / 0.25);
  background: rgb(244 63 94 / 0.1);
  color: #fda4af;
}
</style>
