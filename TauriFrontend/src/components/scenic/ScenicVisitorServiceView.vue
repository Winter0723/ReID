<script setup lang="ts">
import { reactive, ref, watch, onUnmounted } from "vue";
import { apiRequest, apiBlob } from "../../lib/api/http";
import { useWorkbenchStore } from "../../stores/workbench";
const store = useWorkbenchStore();
const emit = defineEmits<{ (event: "select-tourist", id: number): void }>();
interface Config {
  enabled: boolean;
  description: string;
  consent_text: string;
  consent_version: string;
  price_cents: number;
  entry_url?: string;
}
interface Production {
  id: number;
  session_id: number;
  stage: string;
  identity_verified?: boolean;
  message: string;
  reason: string;
  error: string;
  vlog_id?: number;
  draft_id?: number;
  order: null | { id: number; order_no: string; amount_cents: number; status: string; payment_provider?: string };
}
const config = reactive<Config>({
  enabled: false,
  description: "",
  consent_text: "",
  consent_version: "visitor-v1",
  price_cents: 990,
});
const requests = ref<Production[]>([]),
  canConfirmPayment = ref(false),
  error = ref(""),
  message = ref(""),
  qr = ref(""),
  busy = ref(false);
const selectedVlogs = reactive<Record<number, string>>({});
const reasons: Record<string, string> = {
  no_material: "本次游览没有可用素材",
  identity_unverified: "请现场核验游客本人、票据、当前登录账号与登记照片一致",
  identity_review: "人物一致性需要复核",
  index_timeout: "素材入库超时",
  render_failed: "部分或全部画幅失败",
  render_timeout: "渲染超时，需要重试",
  production_failed: "制作失败",
  recording_failed: "录像封存失败",
};
let generation = 0;
let timer: ReturnType<typeof setTimeout> | undefined;
let alive = true;
async function refreshQueue() {
  const pid = store.scenicProjectId, current = generation;
  try {
    if (pid && !busy.value) {
      const queue = await apiRequest<{ requests: Production[]; can_confirm_payment: boolean }>(
        `/api/v1/scenic/projects/${pid}/visitor-queue`, { authScope: "scenic" });
      if (alive && current === generation) {
        requests.value = queue.requests;
        canConfirmPayment.value = queue.can_confirm_payment;
      }
    }
  } catch (e) {
    if (alive && current === generation) error.value = (e as Error).message;
  } finally {
    if (alive) timer = setTimeout(refreshQueue, 5000);
  }
}
timer = setTimeout(refreshQueue, 5000);
async function load() {
  const pid = store.scenicProjectId,
    current = ++generation;
  if (!pid) return;
  busy.value = true;
  error.value = "";
  try {
    const [next, queue] = await Promise.all([
      apiRequest<Config>(`/api/v1/scenic/projects/${pid}/visitor-service`, {
        authScope: "scenic",
      }),
      apiRequest<{ requests: Production[]; can_confirm_payment: boolean }>(
        `/api/v1/scenic/projects/${pid}/visitor-queue`,
        { authScope: "scenic" },
      ),
    ]);
    if (current !== generation) return;
    Object.assign(config, next);
    requests.value = queue.requests;
    canConfirmPayment.value = queue.can_confirm_payment;
  } catch (e) {
    if (current === generation) error.value = (e as Error).message;
  } finally {
    if (current === generation) busy.value = false;
  }
}
async function save() {
  busy.value = true;
  error.value = "";
  try {
    await apiRequest(
      `/api/v1/scenic/projects/${store.scenicProjectId}/visitor-service`,
      { method: "PUT", authScope: "scenic", json: config },
    );
    message.value = "游客服务配置已保存；已登记游客保留登记时的价格。";
  } catch (e) {
    error.value = (e as Error).message;
  } finally {
    busy.value = false;
  }
}
async function showQr() {
  const pid = store.scenicProjectId;
  try {
    const blob = await apiBlob(
      `/api/v1/scenic/projects/${pid}/visitor-entry-qr`,
      { authScope: "scenic" },
    );
    if (pid !== store.scenicProjectId) return;
    if (qr.value) URL.revokeObjectURL(qr.value);
    qr.value = URL.createObjectURL(blob);
  } catch (e) {
    error.value = (e as Error).message;
  }
}
const confirming = ref<Production>();
async function confirmPayment() {
  const row = confirming.value;
  if (!row?.order || !canConfirmPayment.value) return;
  busy.value = true;
  error.value = "";
  try {
    await apiRequest(`/api/v1/scenic/orders/${row.order.id}/mark-paid`, { method: "POST", authScope: "scenic" });
    confirming.value = undefined;
    message.value = "付款已确认，游客刷新后即可下载领取。";
    await load();
  } catch (e) { error.value = (e as Error).message; }
  finally { busy.value = false; }
}
async function resume(row: Production, action: "retry" | "publish" | "verify") {
  busy.value = true;
  error.value = "";
  try {
    await apiRequest(`/api/v1/scenic/visitor-requests/${row.id}/resume`, {
      method: "POST",
      authScope: "scenic",
      json: {
        action,
        vlog_id:
          action === "publish"
            ? Number(selectedVlogs[row.id] || row.vlog_id)
            : null,
      },
    });
    await load();
  } catch (e) {
    error.value = (e as Error).message;
  } finally {
    busy.value = false;
  }
}
watch(
  () => store.scenicProjectId,
  () => {
    if (qr.value) URL.revokeObjectURL(qr.value);
    qr.value = "";
    requests.value = [];
    canConfirmPayment.value = false;
    confirming.value = undefined;
    message.value = "";
    void load();
  },
  { immediate: true },
);
onUnmounted(() => {
  alive = false;
  clearTimeout(timer);
  generation++;
  if (qr.value) URL.revokeObjectURL(qr.value);
});
</script>
<template>
  <section class="visitor-service">
    <header>
      <h3>游客自助服务</h3>
      <button :disabled="busy" @click="load">刷新</button>
    </header>
    <p v-if="error" role="alert" class="error">{{ error }}</p>
    <p v-if="message" role="status">{{ message }}</p>
    <form @submit.prevent="save">
      <label class="toggle"
        ><input
          v-model="config.enabled"
          type="checkbox"
        />开放当前景区游客入口</label
      ><label
        >服务介绍<textarea
          v-model="config.description"
          required
          maxlength="1000"
        /></label
      ><label
        >拍摄授权说明<textarea
          v-model="config.consent_text"
          required
          maxlength="3000"
        />
      </label>
      <div class="fields">
        <label
          >授权版本<input
            v-model="config.consent_version"
            required
            maxlength="32" /></label
        ><label
          >完整视频售价（分）<input
            v-model.number="config.price_cents"
            type="number"
            min="1"
            max="100000"
            required
        /></label>
      </div>
      <button :disabled="busy" type="submit">保存配置</button>
    </form>
    <div class="entry">
      <p>
        入园链接：<a :href="config.entry_url" target="_blank" rel="noopener">{{
          config.entry_url
        }}</a>
      </p>
      <button @click="showQr">显示入园二维码</button
      ><img v-if="qr" :src="qr" alt="当前景区游客入园二维码" />
    </div>
    <section v-if="confirming?.order" role="dialog" aria-label="确认游客付款" class="entry">
      <h3>确认游客付款</h3>
      <p>订单 {{ confirming.order.order_no }} · ¥{{ (confirming.order.amount_cents / 100).toFixed(2) }}。确认后将开放完整视频下载，请核对游客申请。</p>
      <div class="actions"><button :disabled="busy" @click="confirmPayment">确认，开放下载</button><button :disabled="busy" @click="confirming = undefined">取消</button></div>
    </section>
    <header>
      <h3>自助制作请求</h3>
      <span
        >{{
          requests.filter((r) => r.stage === "review" || (r.order?.payment_provider === "visitor_manual" && r.order.status === "pending")).length
        }}
        项需要处理</span
      >
    </header>
    <p>
      完成现场身份与拍摄授权核验后，游客可自行挑选匹配画面、精剪、制作与试看；景区负责人或管理员处理付款确认。
      异常请求仍可打开匹配与精剪协助处理，已有订单的交付版本不可替换。
    </p>
    <article v-for="row in requests" :key="row.id">
      <header>
        <b>游客游览 #{{ row.id }} · 登记 #{{ row.session_id }}</b
        ><span>{{ row.message }}</span>
      </header>
      <p v-if="row.reason">{{ reasons[row.reason] || row.reason }}</p>
      <details v-if="row.error">
        <summary>查看处理详情</summary>
        <p>{{ row.error }}</p>
      </details>
      <small
        >草稿 {{ row.draft_id || "—" }} · 成片 {{ row.vlog_id || "—" }}</small
      >
      <p v-if="row.order">订单 {{ row.order.order_no }} · ¥{{ (row.order.amount_cents / 100).toFixed(2) }} · {{ row.order.status === 'paid' ? '已确认付款' : row.order.payment_provider === 'visitor_manual' ? '等待管理员确认付款' : '待支付' }}</p>
      <div class="actions">
        <button v-if="row.identity_verified === false" :disabled="busy" @click="resume(row, 'verify')">已现场核验本人、账号、票据与照片</button>
        <button v-if="row.order?.payment_provider === 'visitor_manual' && row.order.status === 'pending'" :disabled="busy || !canConfirmPayment" :title="canConfirmPayment ? '核对申请后开放下载' : '需要当前景区负责人或管理员权限'" @click="confirming = row">确认游客付款</button>
        <button @click="emit('select-tourist', row.session_id)">
          打开匹配与精剪</button
        ><button
          v-if="row.stage === 'review' && !row.order"
          :disabled="busy"
          @click="resume(row, 'retry')"
        >
          重新匹配</button
        ><template v-if="!row.order && row.stage === 'review'"
          ><input
            v-model="selectedVlogs[row.id]"
            type="number"
            min="1"
            aria-label="已复核成片 ID"
            placeholder="已复核成片 ID"
          /><button
            :disabled="busy || !Number(selectedVlogs[row.id] || row.vlog_id)"
            @click="resume(row, 'publish')"
          >
            已复核，发布双版视频
          </button></template
        >
      </div>
    </article>
    <p v-if="!requests.length && !busy">暂无自助制作请求。</p>
  </section>
</template>
<style scoped>
.visitor-service {
  color: #e4e4e7;
  display: grid;
  gap: 18px;
  max-width: 1100px;
}
.visitor-service header,
.fields,
.actions {
  display: flex;
  gap: 14px;
  align-items: center;
  justify-content: space-between;
}
.visitor-service h3,
.visitor-service p {
  margin: 0;
}
.visitor-service p,
small {
  font-size: 13px;
  line-height: 1.8;
  color: #a1a1aa;
}
.visitor-service form,
.visitor-service label {
  display: grid;
  gap: 10px;
}
.visitor-service form,
.entry,
article {
  padding: 20px;
  border: 1px solid #ffffff1a;
  border-radius: 10px;
  display: grid;
  gap: 16px;
  background: #09090b44;
}
.visitor-service label {
  font-size: 13px;
}
.visitor-service label.toggle {
  display: flex;
  align-items: center;
}
.visitor-service input,
.visitor-service textarea {
  background: #18181b;
  border: 1px solid #ffffff26;
  border-radius: 6px;
  padding: 10px;
  color: #e4e4e7;
}
.visitor-service textarea {
  min-height: 72px;
  resize: vertical;
}
.visitor-service button {
  background: #ffffff10;
  border: 1px solid #ffffff26;
  color: #e4e4e7;
  padding: 9px 14px;
  border-radius: 7px;
  cursor: pointer;
}
.visitor-service button:disabled {
  opacity: 0.5;
}
.visitor-service button[type="submit"] {
  justify-self: start;
  background: #245947;
}
.fields > label {
  flex: 1;
}
.entry img {
  width: 210px;
  background: white;
}
.entry a {
  overflow-wrap: anywhere;
  color: #86efac;
}
.actions {
  justify-content: flex-start;
  flex-wrap: wrap;
}
.actions input {
  width: 180px;
}
.visitor-service .error {
  color: #fda4af;
}
article details p {
  overflow-wrap: anywhere;
}
</style>
