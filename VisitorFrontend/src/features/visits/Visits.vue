<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { api, ApiError, date, type Visit } from "../../shared/api";
import { useSession } from "../auth/session";
const session = useSession();
defineProps<{ works?: boolean }>();
const rows = ref<Visit[]>([]),
  error = ref(""),
  loading = ref(true),
  signedOut = ref(false);
const worksRows = computed(() => rows.value.filter((v) => v.video_available));
async function load() {
  loading.value = true;
  error.value = "";
  signedOut.value = false;
  try {
    rows.value = (await api<{ visits: Visit[] }>("/visits")).visits;
    if (!session.registrationPath && rows.value.length) session.rememberSite(rows.value[0].project_id);
  } catch (e) {
    signedOut.value = e instanceof ApiError && e.status === 401;
    error.value = (e as Error).message;
  } finally {
    loading.value = false;
  }
}
onMounted(load);
</script>
<template>
  <RouterLink v-if="session.registrationPath" :to="session.registrationPath" class="back">← 返回入园登记</RouterLink>
  <h1 class="page-title">{{ works ? "我的作品" : "我的游览" }}</h1>
  <p>
    {{ works ? "让每一段回忆，都有迹可循。" : "安心享受风景，精彩留给我们。" }}
  </p>
  <div v-if="loading" class="notice" role="status">正在寻找您的游览记录…</div>
  <div
    v-else-if="error"
    class="notice"
    :class="{ error: !signedOut }"
    role="alert"
  >
    <b>{{ signedOut ? "从景区入口开始" : "暂时无法加载" }}</b>
    <p>
      {{
        signedOut
          ? "请打开景区入园链接或扫描二维码。已登记过的游客，请用原浏览器打开同一景区入口后查看。"
          : error
      }}
    </p>
    <button class="text-button" @click="load">重新加载</button>
  </div>
  <template v-else
    ><div v-if="!(works ? worksRows : rows).length" class="empty">
      <span class="empty-mark">↗</span>
      <h2>{{ works ? "回忆正在路上" : "还没有游览记录" }}</h2>
      <p>
        {{
          works
            ? "结束游览并完成制作后，作品会出现在这里。"
            : "扫描景区入园二维码，开始记录您的旅途。"
        }}
      </p>
      <RouterLink v-if="works" to="/visits" class="text-button"
        >查看我的游览</RouterLink
      >
    </div>
    <RouterLink
      v-for="visit in works ? worksRows : rows"
      :key="visit.id"
      :to="`/visits/${visit.id}`"
      class="visit-row"
      ><div>
        <h2>{{ visit.name }}</h2>
        <small>{{ date(visit.created_at) }}</small>
        <p>
          {{
            visit.order?.status === "paid"
              ? "已购视频 · 随时回看"
              : visit.message
          }}
        </p>
      </div>
      <span aria-hidden="true">↗</span></RouterLink
    >
  </template>
</template>
