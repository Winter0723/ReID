<script setup lang="ts">
import { computed, ref, toRef } from "vue";
import { LogIn, LogOut, Settings2, UserPlus, X } from "@lucide/vue";

import { useDialogFocus } from "../../lib/dialogFocus";
import { useWorkbenchStore } from "../../stores/workbench";

const props = defineProps<{
  open: boolean;
}>();

const emit = defineEmits<{
  (event: "close"): void;
}>();

const store = useWorkbenchStore();
const dialog = ref<HTMLElement | null>(null);
const isLoginMode = ref(true);
const loginForm = computed(() => store.coreLogin);

function close(): void {
  emit("close");
}

useDialogFocus(toRef(props, "open"), dialog, close);

async function submitRegister(): Promise<void> {
  if (store.busy) return;
  await store.register("core");
  if (!store.error) isLoginMode.value = true;
}

async function submitLogin(): Promise<void> {
  if (store.busy) return;
  await store.login("core");
  if (!store.error) close();
}

function logout(): void {
  store.logout("core");
  close();
}
</script>

<template>
  <div v-if="open" class="modal-overlay" @click="close">
    <div
      ref="dialog"
      class="modal-content"
      role="dialog"
      aria-modal="true"
      aria-labelledby="core-account-dialog-title"
      tabindex="-1"
      @click.stop
    >
      <div class="mb-6 flex items-center justify-between">
        <h2 id="core-account-dialog-title" class="text-lg font-bold text-white">
          {{ store.isAuthenticated ? "账户已授权" : (isLoginMode ? "验证身份" : "注册新账号") }}
        </h2>
        <button class="text-zinc-500 transition-colors hover:text-white" type="button" title="关闭" aria-label="关闭账户窗口" @click="close">
          <X :size="18" />
        </button>
      </div>

      <p v-if="store.error" role="alert" class="mb-4 rounded-lg bg-rose-500/10 p-3 text-sm text-rose-300">{{ store.error }}</p>
      <p v-else-if="store.message" role="status" aria-live="polite" class="mb-4 rounded-lg bg-cyan-500/10 p-3 text-sm text-cyan-200">{{ store.message }}</p>
      <div v-if="!store.isAuthenticated" :aria-busy="store.busy">
        <template v-if="isLoginMode">
          <form class="space-y-4" @submit.prevent="submitLogin">
            <input v-model="loginForm.account" class="field w-full rounded-xl border-white/[0.06] bg-zinc-900/50 text-sm" placeholder="用户名或邮箱" autocomplete="username" />
            <input v-model="loginForm.password" class="field w-full rounded-xl border-white/[0.06] bg-zinc-900/50 text-sm" placeholder="密码" type="password" autocomplete="current-password" />
            <button class="primary-button w-full justify-center rounded-xl py-3 text-sm" type="submit" :disabled="store.busy">
              <LogIn :size="16" />
              <span>登录系统</span>
            </button>
            <div class="pt-2 text-center">
              <button class="text-xs text-cyan-400 hover:text-cyan-300" type="button" @click="isLoginMode = false">没有账号？立即注册</button>
            </div>
          </form>
        </template>

        <template v-else>
          <div class="space-y-4">
            <div class="grid grid-cols-2 gap-3">
              <input v-model="loginForm.email" class="field rounded-xl border-white/[0.06] bg-zinc-900/50 text-xs" placeholder="邮箱" />
              <input v-model="loginForm.code" class="field rounded-xl border-white/[0.06] bg-zinc-900/50 text-xs" placeholder="验证码" />
              <input v-model="loginForm.username" class="field rounded-xl border-white/[0.06] bg-zinc-900/50 text-xs" placeholder="新用户名" />
              <input v-model="loginForm.registerPassword" class="field rounded-xl border-white/[0.06] bg-zinc-900/50 text-xs" placeholder="新密码" type="password" />
            </div>
            <div class="grid grid-cols-2 gap-3">
              <button class="ghost-button justify-center rounded-xl border-white/[0.06] bg-zinc-900/40 py-2.5 text-xs" type="button" :disabled="store.busy" @click="store.requestEmailCode('core', 'register')">
                <UserPlus :size="14" />
                <span>获取验证码</span>
              </button>
              <button class="primary-button justify-center rounded-xl py-2.5 text-xs" type="button" :disabled="store.busy" @click="submitRegister">
                <UserPlus :size="14" />
                <span>注册账号</span>
              </button>
            </div>
            <p v-if="loginForm.debugCode" class="rounded-lg border border-amber-500/20 bg-amber-500/10 px-3 py-2 text-center text-xs text-amber-300">验证码: {{ loginForm.debugCode }}</p>
            <div class="pt-2 text-center">
              <button class="text-xs text-zinc-400 hover:text-zinc-300" type="button" @click="isLoginMode = true">已有账号？返回登录</button>
            </div>
          </div>
        </template>
      </div>

      <div v-else class="space-y-4">
        <div class="rounded-xl border border-white/[0.06] bg-zinc-900/40 p-5">
          <div class="mb-4 text-center">
            <div class="mx-auto mb-3 flex h-16 w-16 items-center justify-center rounded-full bg-zinc-800">
              <UserPlus :size="24" class="text-zinc-500" />
            </div>
            <p class="text-base font-bold text-white">{{ store.activeUser?.username }}</p>
            <p class="mt-1 text-xs text-zinc-400">{{ store.activeUser?.email || "未绑定邮箱" }}</p>
          </div>
          <div class="space-y-2 border-t border-white/[0.06] pt-4 text-xs">
            <div class="flex justify-between gap-4"><span class="text-zinc-500">用户 ID</span><span class="truncate text-zinc-300">{{ store.activeUser?.public_id || "-" }}</span></div>
            <div class="flex justify-between gap-4"><span class="text-zinc-500">系统角色</span><span class="text-zinc-300">{{ store.activeUser?.role || "-" }}</span></div>
            <div class="flex justify-between gap-4"><span class="text-zinc-500">创建时间</span><span class="text-zinc-300">{{ store.activeUser?.created_at || "-" }}</span></div>
          </div>
        </div>
        <div class="rounded-xl border border-white/[0.06] bg-zinc-900/40 p-4">
          <div class="mb-3 flex items-center gap-2 text-sm font-semibold text-white">
            <Settings2 :size="16" class="text-cyan-300" />
            <span>系统设置</span>
          </div>
          <label class="flex items-center justify-between gap-4 py-2 text-xs text-zinc-300">
            <span>自动刷新实时画面</span>
            <input type="checkbox" :checked="store.preferences.autoRefresh" @change="store.updatePreferences({ autoRefresh: ($event.target as HTMLInputElement).checked })" />
          </label>
          <label class="flex items-center justify-between gap-4 py-2 text-xs text-zinc-300">
            <span>刷新间隔</span>
            <select
              class="field w-28 rounded-lg bg-zinc-950/60 px-2 py-1 text-xs"
              :value="store.preferences.livePollIntervalMs"
              @change="store.updatePreferences({ livePollIntervalMs: Number(($event.target as HTMLSelectElement).value) })"
            >
              <option :value="500">0.5 秒</option>
              <option :value="1000">1 秒</option>
              <option :value="2000">2 秒</option>
              <option :value="5000">5 秒</option>
            </select>
          </label>
        </div>
        <button class="ghost-button w-full justify-center rounded-xl py-3 text-sm text-rose-400 hover:border-rose-500/30 hover:bg-rose-500/10" type="button" @click="logout">
          <LogOut :size="16" />
          <span>退出登录</span>
        </button>
      </div>
    </div>
  </div>
</template>
