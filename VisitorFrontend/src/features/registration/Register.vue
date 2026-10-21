<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { api, type Site, type Visit } from "../../shared/api";
import { useSession } from "../auth/session";
const route = useRoute(),
  router = useRouter(),
  session = useSession();
const site = ref<Site>(),
  error = ref(""),
  busy = ref(false),
  consent = ref(false),
  file = ref<File>(),
  preview = ref("");
const box = ref<number[]>([]),
  anchor = ref<number[] | null>(null),
  photo = ref<HTMLImageElement>();
const keyName = `visitor.registration.${route.params.id}`;
// This is an idempotency key, not an authentication credential. WebViews on
// LAN HTTP do not expose randomUUID; getRandomValues is available there.
function registrationKey(): string {
  try {
    const saved = sessionStorage.getItem(keyName);
    if (saved) return saved;
  } catch { /* Some WebViews disable session storage. */ }
  const bytes = new Uint8Array(16);
  if (globalThis.crypto?.getRandomValues) globalThis.crypto.getRandomValues(bytes);
  else for (let i = 0; i < bytes.length; i++) bytes[i] = Math.floor(Math.random() * 256);
  const key = `${Date.now().toString(36)}-${Array.from(bytes, b => b.toString(16).padStart(2, "0")).join("")}`;
  try { sessionStorage.setItem(keyName, key); } catch { /* Keep it for this page's retries. */ }
  return key;
}
const requestKey = registrationKey();
const boxStyle = computed(() =>
  box.value.length === 4
    ? {
        left: `${box.value[0] * 100}%`,
        top: `${box.value[1] * 100}%`,
        width: `${(box.value[2] - box.value[0]) * 100}%`,
        height: `${(box.value[3] - box.value[1]) * 100}%`,
      }
    : {},
);
onMounted(async () => {
  try {
    site.value = await api<Site>(`/sites/${route.params.id}`);
    if (session.runtime?.auth_provider === "browser") await session.browserLogin(site.value.id);
    else await session.check();
  } catch (e) {
    error.value = (e as Error).message;
  }
});
function choose(event: Event) {
  const next = (event.target as HTMLInputElement).files?.[0];
  if (!next) return;
  if (next.size > 10 * 1024 * 1024) {
    error.value = "请选择 10 MB 以内的照片";
    return;
  }
  if (preview.value) URL.revokeObjectURL(preview.value);
  file.value = next;
  preview.value = URL.createObjectURL(next);
  box.value = [];
  error.value = "";
}
function point(event: PointerEvent) {
  const rect = photo.value!.getBoundingClientRect();
  return [
    Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width)),
    Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height)),
  ];
}
function down(event: PointerEvent) {
  anchor.value = point(event);
  (event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
  box.value = [];
}
function move(event: PointerEvent) {
  if (!anchor.value) return;
  const [x, y] = point(event),
    [a, b] = anchor.value;
  box.value = [Math.min(x, a), Math.min(y, b), Math.max(x, a), Math.max(y, b)];
}
function up(event: PointerEvent) {
  if (anchor.value) move(event);
  anchor.value = null;
}
async function submit() {
  if (!site.value || !file.value || box.value.length !== 4 || !consent.value)
    return;
  busy.value = true;
  error.value = "";
  try {
    const data = new FormData();
    data.set("file", file.value);
    data.set("request_key", requestKey);
    data.set("consent_version", site.value.consent_version);
    data.set("consent_granted", "true");
    data.set("target_box", JSON.stringify(box.value));
    const visit = await api<Visit>(`/sites/${site.value.id}/visits`, {
      method: "POST",
      body: data,
    });
    try { sessionStorage.removeItem(keyName); } catch { /* Registration already succeeded. */ }
    await router.replace(`/visits/${visit.id}`);
  } catch (e) {
    error.value = (e as Error).message;
  } finally {
    busy.value = false;
  }
}
onUnmounted(() => {
  if (preview.value) URL.revokeObjectURL(preview.value);
});
</script>
<template>
  <RouterLink :to="`/sites/${route.params.id}`" class="back"
    >← 返回景区</RouterLink
  >
  <h1 class="page-title">上传本人照片</h1>
  <p>请上传当天着装的清晰半身或全身照，<br />帮助我们找到属于您的游览画面。</p>
  <div v-if="error" class="notice error" role="alert">{{ error }}</div>
  <template v-if="site && session.authenticated">
    <label class="upload" :class="{ compact: preview }"
      ><span>{{ preview ? "更换照片" : "＋ 拍摄或选择照片" }}</span
      ><input
        type="file"
        accept="image/jpeg,image/png,image/webp"
        @change="choose"
    /></label>
    <template v-if="preview"
      ><p class="caption">在照片上拖动，框选且仅包含本人。</p>
      <div
        class="photo-selector"
        @pointerdown="down"
        @pointermove="move"
        @pointerup="up"
        @pointercancel="anchor = null"
      >
        <img
          ref="photo"
          :src="preview"
          alt="登记参照照片"
          draggable="false"
        /><span v-if="box.length === 4" class="selection" :style="boxStyle" />
      </div>
      <button class="text-button" @click="box = [0, 0, 1, 1]">
        照片只有我，选择整张照片
      </button></template
    >
    <div class="notice">
      <b>照片要求</b>
      <p>
        仅包含本人，光线充足、人物清晰。<br />不要只上传头像；多人照片请准确框选。
      </p>
    </div>
    <label class="consent"
      ><input type="checkbox" v-model="consent" /><span
        >我已阅读并同意以下拍摄授权说明</span
      ></label
    >
    <details class="consent-details">
      <summary>查看个人信息使用授权说明</summary>
      <p>{{ site.consent_text }}</p>
      <small>授权版本：{{ site.consent_version }}</small>
    </details>
    <button
      class="primary"
      :disabled="busy || !file || !consent || box.length !== 4"
      @click="submit"
    >
      {{ busy ? "正在核验照片…" : "提交登记" }}
    </button> </template
  ><RouterLink v-else-if="site" class="primary" :to="`/sites/${site.id}`">
    返回景区入口继续登记
  </RouterLink>
  <p v-else-if="!error">正在加载…</p>
</template>
