import { defineStore } from "pinia";
import { computed, ref } from "vue";
import { api, ApiError, API } from "../../shared/api";
export const useSession = defineStore("visitor-session", () => {
  const authenticated = ref(false);
  const authProvider = ref<string>();
  const lastSiteId = ref<string>();
  try { lastSiteId.value = localStorage.getItem("visitor.lastSiteId") || undefined; } catch { /* Optional navigation hint. */ }
  function rememberSite(id: string | number) {
    if (!/^[1-9]\d*$/.test(String(id))) return;
    lastSiteId.value = String(id);
    try { localStorage.setItem("visitor.lastSiteId", String(id)); } catch { /* Optional navigation hint. */ }
  }
  const registrationPath = computed(() => lastSiteId.value ? `/sites/${lastSiteId.value}` : undefined);
  type Identity = { authenticated: boolean; auth_provider?: string };
  function setIdentity(identity: Identity) {
    authenticated.value = identity.authenticated;
    authProvider.value = identity.auth_provider;
  }
  const runtime = ref<{ auth_provider: string; payment_provider: string; testing: boolean; browser_login_available?: boolean; wechat_login_available?: boolean }>();
  async function loadRuntime() {
    runtime.value = await api("/runtime");
  }
  async function testLogin(projectId: number, visitor: string, inviteCode: string) {
    const identity = await api<Identity>("/auth/test", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project_id: projectId, visitor, invite_code: inviteCode }) });
    setIdentity(identity);
  }
  async function browserLogin(projectId: number) {
    const identity = await api<Identity>("/auth/browser", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project_id: projectId }) });
    setIdentity(identity);
  }
  async function check() {
    try {
      setIdentity(await api<Identity>("/me"));
    } catch (error) {
      authenticated.value = false;
      authProvider.value = undefined;
      if (!(error instanceof ApiError) || error.status !== 401) throw error;
    }
    return authenticated.value;
  }
  function login(projectId: number) {
    window.location.assign(`${API}/auth/wechat?project_id=${projectId}`);
  }
  return { authenticated, authProvider, registrationPath, rememberSite, runtime, loadRuntime, testLogin, browserLogin, check, login };
});
