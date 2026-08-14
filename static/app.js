/* 瞎掰王 · 前端邏輯（輪詢式，適合局網派對） */
(function () {
  "use strict";

  var pid = localStorage.getItem("bk_pid") || "";
  var app = document.getElementById("app");
  var playerbar = document.getElementById("playerbar");
  var state = null;
  var prevRound = null;
  var lastSig = null;      // 上次渲染的狀態指紋，用來避免不必要的重繪
  var draftName = "";      // 保留使用者正在輸入的名字

  // 猜測階段本地選擇
  var pickSuspect = null;
  var pickCallout = null;

  // ---- 工具 -------------------------------------------------------------
  function el(html) {
    var d = document.createElement("div");
    d.innerHTML = html.trim();
    return d.firstChild;
  }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }
  function diffStars(n) { return "★".repeat(n) + "☆".repeat(3 - n); }

  function flash(msg) {
    var f = el('<div class="flash">' + esc(msg) + "</div>");
    document.body.appendChild(f);
    setTimeout(function () { f.remove(); }, 1800);
  }

  function api(path, body) {
    return fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(Object.assign({ pid: pid }, body || {})),
    })
      .then(function (r) { return r.json(); })
      .then(function (j) {
        if (j && j.ok === false && j.error) flash(j.error);
        return j;
      })
      .then(function (j) { poll(); return j; })
      .catch(function () {});
  }

  // ---- 輪詢 -------------------------------------------------------------
  function poll() {
    fetch("/api/state?pid=" + encodeURIComponent(pid) + "&t=" + Date.now())
      .then(function (r) { return r.json(); })
      .then(function (s) { state = s; render(); playReactions(s); })
      .catch(function () {});
  }

  // ---- 即時吐槽「聽你在扯淡！」-----------------------------------------
  var shownReactions = {};   // 已經播過的 reaction id，避免每次輪詢重播
  var lastReactAt = 0;       // 本機節流

  // 按自己的頭像 → 廣播給所有人
  function sendReaction() {
    var now = Date.now();
    if (now - lastReactAt < 1500) return;   // 跟伺服器的冷卻對齊，按太快就忽略
    lastReactAt = now;
    fetch("/api/react", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pid: pid }),
    }).catch(function () {});
    bumpSelf();
  }

  // 自己按下去的即時回饋（不等伺服器來回）
  function bumpSelf() {
    var mine = document.querySelector(".pb-item.you");
    if (!mine) return;
    mine.classList.remove("bump");
    void mine.offsetWidth;   // 強制 reflow 讓動畫可以重播
    mine.classList.add("bump");
  }

  function reactionLayer() {
    var layer = document.getElementById("reactions");
    if (!layer) {
      layer = el('<div id="reactions" class="reaction-layer"></div>');
      document.body.appendChild(layer);
    }
    return layer;
  }

  function playReactions(s) {
    if (!s || !s.reactions) return;
    var alive = {};
    s.reactions.forEach(function (r) {
      alive[r.id] = true;
      if (shownReactions[r.id]) return;
      shownReactions[r.id] = true;
      showReactionBubble(r);
    });
    // 伺服器已經過期的就從紀錄裡移除，避免無限長大
    Object.keys(shownReactions).forEach(function (id) {
      if (!alive[id]) delete shownReactions[id];
    });
  }

  function showReactionBubble(r) {
    var from = findPlayer(r.from_id);
    if (!from) return;
    var bubble = el(
      '<div class="reaction-bubble">' +
        '<span class="avatar" style="width:34px;height:34px;font-size:20px;background:' + from.color + '">' + from.avatar + "</span>" +
        '<span class="rx-text">' + esc(from.name) + "：" + esc(r.text) + "</span>" +
      "</div>"
    );
    // 左右錯開，同時好幾則才不會疊在一起
    bubble.style.left = (8 + Math.random() * 30) + "%";
    reactionLayer().appendChild(bubble);
    setTimeout(function () { bubble.remove(); }, 2600);
  }

  // 狀態指紋：只在會影響畫面的欄位改變時才重繪，避免每秒輪詢造成閃爍/輸入被清空
  function sigOf(s) {
    if (!s) return "";
    var pl = (s.players || [])
      .map(function (p) { return [p.id, p.name, p.score, p.is_host].join(","); })
      .join("|");
    var extra = "";
    if (s.phase === "reading" && s.reading) extra = "r" + s.reading.remaining + "," + s.reading.is_honest;
    if (s.phase === "speaking" && s.speaking) extra = "s" + s.speaking.current_id + "," + s.speaking.index;
    if (s.phase === "guessing" && s.guessing) extra = "g" + s.guessing.is_guesser;
    if (s.phase === "result") extra = "res";
    // 卡片題目 + 是否已翻面（跳過換題、開始倒數翻面時都要重繪）
    var card = s.card ? s.card.topic + "," + (s.card.description ? 1 : 0) : "";
    var role = s.you ? s.you.role : "";
    return [s.phase, s.round_no, s.in_game, role, pl, card, extra, s.next_guesser_id].join("#");
  }

  // ---- 主渲染 -----------------------------------------------------------
  function render() {
    if (!state) return;

    var sig = sigOf(state);
    if (sig === lastSig) return;   // 沒有實質變化就不重繪
    lastSig = sig;

    // 未加入 → 一律顯示加入畫面
    if (!state.in_game) {
      playerbar.classList.add("hidden");
      return renderJoin();
    }

    // 進到有卡片的階段時重置本地猜測選擇
    if (state.round_no !== prevRound) { pickSuspect = null; pickCallout = null; }

    switch (state.phase) {
      case "lobby": renderLobby(); break;
      case "preview": renderPreview(); break;
      case "reading": renderReading(); break;
      case "speaking": renderSpeaking(); break;
      case "guessing": renderGuessing(); break;
      case "result": renderResult(); break;
      default: renderLobby();
    }

    renderPlayerbar();
    prevRound = state.round_no;
  }

  // ---- 加入畫面 ---------------------------------------------------------
  function renderJoin() {
    app.innerHTML = "";
    app.appendChild(el('<h1 class="logo">瞎掰王</h1>'));
    app.appendChild(el('<p class="subtitle">呼朋引伴一起來瞎掰吧～</p>'));

    var panel = el('<div class="panel"></div>');
    panel.appendChild(el('<div class="section-title">輸入你的名字</div>'));
    var input = el('<input class="text" maxlength="12" placeholder="例如：小明" />');
    input.value = draftName;
    input.oninput = function () { draftName = input.value; };
    panel.appendChild(input);
    var btn = el('<button class="btn primary">加入遊戲</button>');
    btn.onclick = function () {
      var name = input.value.trim();
      api("/api/join", { name: name }).then(function (j) {
        if (j && j.pid) {
          pid = j.pid;
          localStorage.setItem("bk_pid", pid);
          poll();
        }
      });
    };
    panel.appendChild(btn);
    app.appendChild(panel);

    var n = (state && state.players) ? state.players.length : 0;
    app.appendChild(el('<p class="hint">目前已有 ' + n + ' 位玩家在房間裡</p>'));
    if (n > 0) app.appendChild(playersPreview());
  }

  function playersPreview() {
    var wrap = el('<div class="panel" style="display:flex;flex-wrap:wrap;gap:10px;justify-content:center"></div>');
    state.players.forEach(function (p) {
      wrap.appendChild(el(
        '<div style="text-align:center;width:60px">' +
          '<div class="avatar md" style="background:' + p.color + '">' + p.avatar + "</div>" +
          '<div style="font-size:12px;margin-top:2px">' + esc(p.name) + (p.is_host ? " 👑" : "") + "</div>" +
        "</div>"
      ));
    });
    return wrap;
  }

  // ---- 大廳 -------------------------------------------------------------
  function renderLobby() {
    playerbar.classList.add("hidden");
    app.innerHTML = "";
    app.appendChild(el('<h1 class="logo">瞎掰王</h1>'));
    app.appendChild(el('<p class="subtitle">等待玩家加入…</p>'));

    app.appendChild(youBadge(false));
    app.appendChild(playersPreview());
    app.appendChild(el('<p class="hint">大聰明會依上面的座位順序輪流當</p>'));
    var ng = nextGuesserHint();
    if (ng) app.appendChild(ng);

    var need = state.min_players;
    if (state.you.is_host) {
      var btn = el('<button class="btn primary">開始遊戲</button>');
      if (state.players.length < need) {
        btn.disabled = true;
        btn.textContent = "至少要 " + need + " 人（還差 " + (need - state.players.length) + " 人）";
      }
      btn.onclick = function () { api("/api/start"); };
      app.appendChild(btn);
      app.appendChild(resetScoresBtn());
      app.appendChild(el('<p class="hint">你是房主 👑，由你按開始</p>'));
    } else {
      app.appendChild(el('<p class="hint">等房主按下「開始遊戲」…</p>'));
    }

    app.appendChild(el('<p class="hint" style="margin-top:18px">分享網址給朋友：用同一個 Wi-Fi 開啟這個頁面就能加入</p>'));
  }

  // 預告下一位大聰明（依座位固定輪換）
  function nextGuesserHint() {
    var g = findPlayer(state.next_guesser_id);
    if (!g) return null;
    return el('<p class="hint">🔍 下一位大聰明：<b>' + esc(g.avatar + " " + g.name) +
      "</b>" + (g.is_you ? "（就是你！）" : "") + "</p>");
  }

  // 房主專用：分數歸零（大家留在房間，不用重新加入）
  function resetScoresBtn() {
    var rs = el('<button class="btn ghost small">🔄 分數歸零，重新計分</button>');
    rs.onclick = function () {
      if (window.confirm("把所有人的分數歸零重新計分？\n（大家都留在房間裡，不用重新加入）")) {
        api("/api/reset_scores").then(function () { flash("分數已歸零，重新開始！"); });
      }
    };
    return rs;
  }

  function youBadge(showRole) {
    var y = state.you;
    var roleHtml = "";
    if (showRole && y.role) {
      roleHtml = '<span class="role role-' + y.role + '">' + esc(y.role_label) + "</span>";
    } else if (showRole && y.spectator) {
      roleHtml = '<span class="role role-spectator">觀戰中 · 下回合加入</span>';
    }
    var badge = el(
      '<div class="rolebadge">' +
        '<div class="avatar md tappable" style="background:' + y.color + '">' + y.avatar + "</div>" +
        '<div class="who">' + esc(y.name) + (y.is_host ? " 👑" : "") + "</div>" +
        roleHtml +
      "</div>"
    );
    // 這裡的頭像也是自己的，一樣可以按了吐槽
    var ava = badge.querySelector(".avatar");
    ava.title = "點我吐槽：聽你在扯淡！";
    ava.onclick = sendReaction;
    return badge;
  }

  // ---- 卡片 HTML --------------------------------------------------------
  function cardFront(card) {
    return (
      '<div class="gamecard">' +
        '<div class="diff">難度 ' + diffStars(card.difficulty) + "</div>" +
        '<div class="label">題　目</div>' +
        '<div class="topic">' + esc(card.topic) + "</div>" +
      "</div>"
    );
  }
  function cardBack(card) {
    return (
      '<div class="gamecard back">' +
        '<div class="diff">難度 ' + diffStars(card.difficulty) + "</div>" +
        '<div class="label">題　目</div>' +
        '<div class="topic">' + esc(card.topic) + "</div>" +
        '<div class="desc">📖 ' + esc(card.description) + "</div>" +
      "</div>"
    );
  }

  // ---- 題目預覽階段（尚未倒數） -----------------------------------------
  function renderPreview() {
    playerbar.classList.remove("hidden");
    app.innerHTML = "";
    app.appendChild(el('<p class="subtitle">第 ' + state.round_no + " 回合 · 準備出題</p>"));
    app.appendChild(el(cardFront(state.card)));
    app.appendChild(el('<p class="center" style="font-size:16px;margin:8px 0">先看看題目～<br>如果大家本來就知道答案，可以換一張 😉</p>'));

    if (state.you.is_host) {
      var row = el('<div class="btn-row"></div>');
      var begin = el('<button class="btn primary small">▶️ 開始回合</button>');
      begin.onclick = function () { api("/api/begin_reading"); };
      var skip = el('<button class="btn ghost small">🔄 跳過換題</button>');
      skip.onclick = function () { api("/api/skip_card"); };
      row.appendChild(begin);
      row.appendChild(skip);
      app.appendChild(row);
      app.appendChild(resetScoresBtn());
    } else {
      app.appendChild(el('<p class="hint">等房主按「開始回合」…</p>'));
    }

    app.appendChild(youBadge(true));
  }

  // ---- 閱讀階段 ---------------------------------------------------------
  function renderReading() {
    playerbar.classList.remove("hidden");
    app.innerHTML = "";
    app.appendChild(el('<p class="subtitle">第 ' + state.round_no + " 回合 · 老實人偷看題目中</p>"));

    var isHonest = state.reading.is_honest;
    if (isHonest && state.card.description) {
      app.appendChild(el('<div class="turnbanner">你是老實人 🤫<br><span style="font-size:15px">記住答案，等一下裝作大家都懂</span></div>'));
      app.appendChild(el(cardBack(state.card)));
    } else {
      app.appendChild(el(cardFront(state.card)));
      app.appendChild(el('<p class="center" style="font-size:17px">老實人正在看題目…<br>其他人先想想怎麼瞎掰 😏</p>'));
    }

    var t = state.reading.remaining;
    app.appendChild(el('<div class="timer"><div class="num">' + t + '</div><div class="cap">秒後開始發言</div></div>'));

    app.appendChild(youBadge(true));

    if (state.you.is_host) {
      var skip = el('<button class="btn ghost small">大家都好了，直接開始</button>');
      skip.onclick = function () { api("/api/skip_reading"); };
      app.appendChild(skip);
    }
  }

  // ---- 發言階段 ---------------------------------------------------------
  function renderSpeaking() {
    playerbar.classList.remove("hidden");
    app.innerHTML = "";
    app.appendChild(el('<p class="subtitle">第 ' + state.round_no + " 回合 · 輪流發言</p>"));

    var sp = state.speaking;
    var cur = findPlayer(sp.current_id);
    if (cur) {
      app.appendChild(el(
        '<div class="turnbanner">🎤 現在發言<br>' +
          '<span class="big">' + cur.avatar + " " + esc(cur.name) + "</span>" +
          '<div style="font-size:14px;color:#8A7658">（第 ' + (sp.index + 1) + " / " + sp.total + " 位）</div>" +
        "</div>"
      ));
    }

    app.appendChild(el(cardFront(state.card)));

    // 老實人在發言階段仍可看到答案提示
    if (state.you.role === "honest" && state.card.description) {
      app.appendChild(el('<div class="gamecard back" style="padding:14px"><div class="desc" style="border:none;margin:0;padding:0">📖 ' + esc(state.card.description) + "</div></div>"));
    }

    app.appendChild(youBadge(true));

    // 換下一位：房主或目前發言者
    var meIsCur = sp.is_current;
    if (state.you.is_host || meIsCur) {
      var nextBtn = el('<button class="btn primary">' + (sp.index + 1 >= sp.total ? "發言結束，換大聰明猜 →" : "下一位發言 →") + "</button>");
      nextBtn.onclick = function () { api("/api/advance"); };
      app.appendChild(nextBtn);
    } else {
      app.appendChild(el('<p class="hint">' + (meIsCur ? "" : "輪到你發言時按鈕會出現，或由房主控制") + "</p>"));
    }

    // 大聰明可以提前進入猜測
    if (sp.is_guesser) {
      var g = el('<button class="btn ghost small">我聽夠了，直接開始猜 🔍</button>');
      g.onclick = function () { api("/api/to_guessing"); };
      app.appendChild(g);
    }
  }

  // ---- 猜測階段 ---------------------------------------------------------
  function renderGuessing() {
    playerbar.classList.remove("hidden");
    app.innerHTML = "";
    app.appendChild(el('<p class="subtitle">第 ' + state.round_no + " 回合 · 大聰明猜測</p>"));
    app.appendChild(el(cardFront(state.card)));

    if (!state.guessing.is_guesser) {
      var g = findPlayer(state.guessing.guesser_id);
      app.appendChild(el('<div class="turnbanner">🔍 大聰明思考中…<br><span style="font-size:15px">' + (g ? g.avatar + " " + esc(g.name) : "") + " 正在猜誰是老實人</span></div>"));
      app.appendChild(youBadge(true));
      return;
    }

    // 大聰明的操作界面
    app.appendChild(el('<div class="section-title">🕵️ 誰是老實人？</div>'));
    app.appendChild(el('<div class="section-sub">選一位你認為「真的懂」的人</div>'));
    app.appendChild(pickGrid("suspect"));

    app.appendChild(el('<div class="section-title" style="margin-top:20px">🃏 聽你在瞎掰！（可選）</div>'));
    app.appendChild(el('<div class="section-sub">最多給一位。給對（瞎掰者）→ 你 +1；給錯（老實人）→ 你 -3。也可以不給。</div>'));
    app.appendChild(pickGrid("callout"));

    var summary = el('<p class="hint" id="pickSummary"></p>');
    app.appendChild(summary);
    updateSummary();

    var confirm = el('<button class="btn primary">確認送出 ✅</button>');
    confirm.onclick = function () {
      if (!pickSuspect) { flash("請先選出你猜的老實人"); return; }
      var sName = (findPlayer(pickSuspect) || {}).name || "";
      var cName = pickCallout ? ((findPlayer(pickCallout) || {}).name || "") : "不給任何人";
      if (!window.confirm("你的答案：\n・老實人 = " + sName + "\n・瞎掰卡 = " + cName + "\n\n確定送出？")) return;
      api("/api/guess", { suspect_id: pickSuspect, callout_id: pickCallout });
    };
    app.appendChild(confirm);
  }

  function pickGrid(kind) {
    var grid = el('<div class="pick-grid"></div>');
    state.guessing.candidates.forEach(function (c) {
      var selSus = pickSuspect === c.id;
      var selCal = pickCallout === c.id;
      var cls = "pick";
      if (kind === "suspect" && selSus) cls += " sel-suspect";
      if (kind === "callout" && selCal) cls += " sel-callout";
      var tag = "";
      if (kind === "suspect" && selSus) tag = '<span class="tag">← 老實人</span>';
      if (kind === "callout" && selCal) tag = '<span class="tag">← 瞎掰卡</span>';
      var item = el(
        '<div class="' + cls + '">' +
          '<div class="avatar md" style="background:' + c.color + ';margin:0 auto">' + c.avatar + "</div>" +
          '<div class="nm">' + esc(c.name) + "</div>" + tag +
        "</div>"
      );
      item.onclick = function () {
        if (kind === "suspect") pickSuspect = selSus ? null : c.id;
        else pickCallout = selCal ? null : c.id;
        renderGuessing();
      };
      grid.appendChild(item);
    });
    return grid;
  }

  function updateSummary() {
    var s = document.getElementById("pickSummary");
    if (!s) return;
    var sName = pickSuspect ? (findPlayer(pickSuspect) || {}).name : "尚未選擇";
    var cName = pickCallout ? (findPlayer(pickCallout) || {}).name : "不給任何人";
    s.textContent = "老實人：" + sName + "　｜　瞎掰卡：" + cName;
  }

  // ---- 結算階段 ---------------------------------------------------------
  function renderResult() {
    playerbar.classList.remove("hidden");
    app.innerHTML = "";
    var r = state.result;
    var honest = findPlayer(r.honest_id);
    var guesser = findPlayer(r.guesser_id);
    var suspect = findPlayer(r.suspect_id);

    app.appendChild(el('<div class="result-emoji">' + (r.you_won ? "🎉" : "😆") + "</div>"));
    app.appendChild(el('<div class="result-title">' + (r.correct ? "大聰明猜中了！" : "被瞎掰過去啦！") + "</div>"));
    app.appendChild(el('<p class="center" style="font-size:18px;margin-top:-4px">本回合贏家：<b>' + esc(r.winner_label) + "</b></p>"));

    // 公布答案：所有人都看得到卡片背面的正確解釋
    if (state.card && state.card.description) {
      app.appendChild(el('<div class="section-title center">📖 正確解釋</div>'));
      app.appendChild(el(cardBack(state.card)));
    }

    var panel = el('<div class="panel"></div>');
    panel.appendChild(resultRow("真正的老實人", honest, '<span class="pill good">老實人</span>'));
    panel.appendChild(resultRow("大聰明的猜測", suspect,
      r.correct ? '<span class="pill good">✅ 猜中</span>' : '<span class="pill bad">❌ 猜錯</span>'));

    // 瞎掰卡結果
    var coText;
    if (!r.callout_id) {
      coText = '<div class="result-row">🃏 瞎掰卡：<b>沒有給任何人</b></div>';
    } else {
      var co = findPlayer(r.callout_id);
      if (r.callout_effect === "hit") {
        coText = '<div class="result-row">🃏 瞎掰卡給了 ' + avatarInline(co) + '　<span class="pill good">抓到瞎掰者！大聰明 +' + r.callout_bonus + "</span></div>";
      } else {
        coText = '<div class="result-row">🃏 瞎掰卡給了 ' + avatarInline(co) + '　<span class="pill bad">竟然是老實人！大聰明 -' + r.callout_penalty + "</span></div>";
      }
    }
    panel.appendChild(el(coText));
    app.appendChild(panel);

    // 分數變化
    var sc = el('<div class="panel"></div>');
    sc.appendChild(el('<div class="section-title">分數變化（難度 ' + r.difficulty + " 分）</div>"));
    state.players.forEach(function (p) {
      var d = r.deltas[p.id] || 0;
      var cls = d > 0 ? "plus" : d < 0 ? "minus" : "zero";
      var txt = d > 0 ? "+" + d : d < 0 ? "" + d : "±0";
      var roleTag = p.role_label ? '<span class="pill" style="background:#f3ebd8">' + esc(p.role_label) + "</span>" : "";
      sc.appendChild(el(
        '<div class="result-row">' + avatarInline(p) + roleTag +
          '<span style="margin-left:auto">' + p.score + ' 分</span>' +
          '<span class="delta ' + cls + '">(' + txt + ")</span>" +
        "</div>"
      ));
    });
    app.appendChild(sc);

    var ng = nextGuesserHint();
    if (ng) app.appendChild(ng);

    if (state.you.is_host) {
      var next = el('<button class="btn primary">下一回合 →</button>');
      next.onclick = function () { api("/api/next_round"); };
      app.appendChild(next);
      var lobby = el('<button class="btn ghost small">結束遊戲，回到大廳</button>');
      lobby.onclick = function () { if (window.confirm("結束本場遊戲？分數會保留但回到大廳")) api("/api/back_to_lobby"); };
      app.appendChild(lobby);
      app.appendChild(resetScoresBtn());
    } else {
      app.appendChild(el('<p class="hint">等房主按「下一回合」…</p>'));
    }
  }

  function resultRow(label, player, right) {
    return el('<div class="result-row"><span style="color:#8A7658">' + label + "：</span>" + avatarInline(player) + '<span style="margin-left:auto">' + (right || "") + "</span></div>");
  }
  function avatarInline(p) {
    if (!p) return "—";
    return '<span class="avatar" style="width:34px;height:34px;font-size:20px;background:' + p.color + '">' + p.avatar + '</span><b style="margin-left:6px">' + esc(p.name) + "</b>";
  }

  // ---- 底部玩家列 -------------------------------------------------------
  function renderPlayerbar() {
    var curId = (state.speaking && state.speaking.current_id) || null;
    playerbar.innerHTML = "";
    state.players.forEach(function (p) {
      var cls = "pb-item";
      if (p.id === curId) cls += " speaking";
      if (p.is_you) cls += " you";
      var item = el(
        '<div class="' + cls + '">' +
          (p.is_host ? '<span class="pb-crown">👑</span>' : "") +
          (p.is_you ? '<span class="pb-rx">📢</span>' : "") +
          '<div class="pb-ava" style="background:' + p.color + '">' + p.avatar + "</div>" +
          '<div class="pb-name">' + esc(p.name) + "</div>" +
          '<div class="pb-score">' + p.score + " 分</div>" +
        "</div>"
      );
      // 按自己的頭像 → 對全場喊「聽你在扯淡！」
      if (p.is_you) {
        item.title = "點我吐槽：聽你在扯淡！";
        item.onclick = sendReaction;
      }
      playerbar.appendChild(item);
    });
  }

  function findPlayer(id) {
    if (!id || !state) return null;
    for (var i = 0; i < state.players.length; i++) if (state.players[i].id === id) return state.players[i];
    return null;
  }

  // ---- 啟動 -------------------------------------------------------------
  poll();
  setInterval(poll, 1000);
})();
