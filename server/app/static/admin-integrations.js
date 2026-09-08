/* Admin-only configuration of product-independent lobby integrations. */
(async () => {
  "use strict";
  const byId = (id) => document.getElementById(id);
  const api = "/admin/integrations";
  let editing = null;
  let editingHasWebhook = false;

  function status(message, error = false) {
    byId("integration-status").textContent = message;
    byId("integration-status").dataset.error = String(error);
  }

  async function request(url, method = "GET", body) {
    const response = await fetch(url, {
      method, credentials: "same-origin", cache: "no-store",
      headers: body === undefined ? {} : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (!response.ok) {
      const messages = {
        401: "ログインし直してください。",
        403: "管理者権限が必要です。",
        404: "連携が見つかりません。配信状況を更新してください。",
        409: "連携数の上限、または Webhook の有効状態を確認してください。",
        422: "連携名と送信先を確認してください。送信先は公開 IP に接続できる HTTPS URL が必要です。",
        429: "操作が集中しています。少し待ってから再実行してください。",
        503: "連携設定を保存・読み込みできません。サーバーの保存先を確認してください。",
      };
      throw new Error(messages[response.status] || "処理に失敗しました（" + response.status + "）。");
    }
    return response.status === 204 ? null : response.json();
  }

  function hideCredentials() {
    byId("integration-credentials").hidden = true;
    byId("integration-api-key").value = "";
    byId("integration-signing-secret").value = "";
  }

  function showCredentials(result) {
    byId("integration-api-key").value = result.api_key;
    byId("integration-signing-secret").value = result.signing_secret;
    byId("integration-credentials").hidden = false;
  }

  function updateUrlInput() {
    const input = byId("integration-url");
    input.disabled = byId("integration-api-only").checked;
    input.required = (!editing || !editingHasWebhook) && !input.disabled;
  }

  function resetForm() {
    editing = null;
    editingHasWebhook = false;
    byId("integration-form").reset();
    byId("integration-edit-note").hidden = true;
    byId("integration-cancel").hidden = true;
    byId("integration-save").textContent = "登録してキーを発行";
    updateUrlInput();
  }

  function button(label, action, danger = false) {
    const element = document.createElement("button");
    element.type = "button";
    element.textContent = label;
    if (danger) element.className = "danger";
    element.addEventListener("click", async () => {
      element.disabled = true;
      try { await action(); }
      catch (error) { status(error.message, true); }
      finally { element.disabled = false; }
    });
    return element;
  }

  function paragraph(parent, text, className = "") {
    const element = document.createElement("p");
    element.className = className;
    element.textContent = text;
    parent.appendChild(element);
  }

  async function refresh() {
    const result = await request(api);
    byId("integration-api-url").textContent = result.api_url;
    const list = byId("integration-list");
    list.replaceChildren();
    if (result.storage_error) status("設定または配信状況の保存に問題があります。サーバーの保存先を確認してください。", true);
    if (!result.integrations.length) paragraph(list, "連携はまだ登録されていません。", "muted");
    for (const item of result.integrations) {
      const itemUrl = api + "/" + item.id;
      const card = document.createElement("article");
      card.className = "integration-card";
      const name = document.createElement("strong");
      name.textContent = item.name;
      card.appendChild(name);
      paragraph(card, (item.enabled ? "有効" : "停止中") + " · " + (item.include_address ? "IP を提供" : "IP を提供しない"));
      paragraph(card, item.allow_posting ? "募集作成を許可" : "一覧取得のみ", "muted");
      paragraph(card, "チャット: " + (item.include_chat ? "履歴取得・通知を許可" : "履歴取得・通知なし") + " / " + (item.allow_chat_posting ? "外部投稿を許可" : "外部投稿不可"), "muted");
      paragraph(card, item.webhook_host ? "通知先: " + item.webhook_host + "（URL の残りは非表示）" : "API のみ", "muted");
      const last = item.last_success_at ? new Date(item.last_success_at * 1000).toLocaleString() : "まだありません";
      if (item.webhook_host) {
        paragraph(card, "最終配信成功: " + last, "muted");
        if (item.last_error) paragraph(card, "配信エラー: " + item.last_error + "（有効中は自動再試行）");
      }
      const actions = document.createElement("div");
      actions.className = "row";
      actions.appendChild(button("編集", async () => {
        hideCredentials();
        editing = item.id;
        editingHasWebhook = Boolean(item.webhook_host);
        byId("integration-name").value = item.name;
        byId("integration-url").value = "";
        byId("integration-api-only").checked = !item.webhook_host;
        byId("integration-enabled").checked = item.enabled;
        byId("integration-address").checked = item.include_address;
        byId("integration-posting").checked = Boolean(item.allow_posting);
        byId("integration-chat").checked = Boolean(item.include_chat);
        byId("integration-chat-posting").checked = Boolean(item.allow_chat_posting);
        byId("integration-edit-note").hidden = false;
        byId("integration-cancel").hidden = false;
        byId("integration-save").textContent = "変更を保存";
        updateUrlInput();
        byId("integration-name").focus();
      }));
      actions.appendChild(button(item.enabled ? "停止" : "再開", async () => {
        await request(itemUrl, "PATCH", { enabled: !item.enabled });
        status(item.enabled ? "通知と API キーの利用を停止しました。" : "連携を再開しました。");
        await refresh();
      }));
      if (item.enabled && item.webhook_host) actions.appendChild(button("テスト通知", async () => {
        await request(itemUrl + "/test", "POST");
        status("テスト通知を予約しました。通常は数秒後に送信します。再試行の待機中はその後に送信します。");
      }));
      actions.appendChild(button("キー再発行", async () => {
        if (!confirm("現在の API キーと署名シークレットは使えなくなります。再発行しますか？")) return;
        hideCredentials();
        showCredentials(await request(itemUrl + "/rotate", "POST"));
        status("キーを再発行しました。連携先の設定を更新してください。");
      }));
      actions.appendChild(button("削除", async () => {
        if (!confirm("「" + item.name + "」を削除しますか？通知が停止し、API キーも無効になります。")) return;
        await request(itemUrl, "DELETE");
        hideCredentials();
        if (editing === item.id) resetForm();
        status("連携を削除しました。");
        await refresh();
      }, true));
      card.appendChild(actions);
      list.appendChild(card);
    }
  }

  try {
    const me = await request("/auth/me");
    if (!me.is_admin) return;
  } catch { return; }
  byId("integrations-panel").hidden = false;
  resetForm();
  byId("integration-api-only").addEventListener("change", updateUrlInput);
  byId("integration-cancel").addEventListener("click", resetForm);
  byId("integration-hide-credentials").addEventListener("click", hideCredentials);
  for (const [buttonId, fieldId] of [
    ["integration-copy-api", "integration-api-key"],
    ["integration-copy-secret", "integration-signing-secret"],
  ]) {
    byId(buttonId).addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(byId(fieldId).value);
        status("コピーしました。");
      } catch {
        byId(fieldId).select();
        status("自動コピーできませんでした。選択されたキーをコピーしてください。", true);
      }
    });
  }
  byId("integration-refresh").addEventListener("click", () => refresh().catch((error) => status(error.message, true)));
  byId("integration-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    byId("integration-save").disabled = true;
    hideCredentials();
    try {
      const body = {
        name: byId("integration-name").value.trim(),
        enabled: byId("integration-enabled").checked,
        include_address: byId("integration-address").checked,
        allow_posting: byId("integration-posting").checked,
        include_chat: byId("integration-chat").checked,
        allow_chat_posting: byId("integration-chat-posting").checked,
      };
      const url = byId("integration-url").value.trim();
      if (byId("integration-api-only").checked) body.webhook_url = "";
      else if (url || !editing) body.webhook_url = url;
      if (editing) {
        await request(api + "/" + editing, "PATCH", body);
        status("連携設定を保存しました。");
      } else {
        showCredentials(await request(api, "POST", body));
        status("連携を登録しました。キーを連携先に保存してください。");
      }
      resetForm();
      await refresh();
    } catch (error) {
      status(error.message, true);
    } finally {
      byId("integration-save").disabled = false;
    }
  });
  window.addEventListener("pagehide", hideCredentials);
  await refresh().catch((error) => status(error.message, true));
})();
