# arXiv:2610.07658v1 実装メモ(GSIS-PBC)

- 作成: 2026-10-15(仕様策定用の抜き書き。式番号は論文の番号)
- 対象: Y. Zhang, Y. Wen, L. Wu, *A pressure-based coupled general synthetic iterative scheme for rarefied gas flow simulation*, arXiv:2610.07658v1 [physics.comp-ph], 2026-10-06, CC BY 4.0
- URL: https://arxiv.org/html/2610.07658v1 (図: `https://arxiv.org/html/2610.07658v1/Figures/<名前>.png`。寸法図は `Tube_size.png`, `DGL_size.png`, `Divertor_size.png`)
- 「仮定」は論文に明記がなく、実装側で決める必要がある項目(§8 に一覧)。

---

## 1. 支配方程式(§2)

- Shakhov モデル(単原子、$\Pr=2/3$)
  - (1) $\partial_t f+\boldsymbol\xi\cdot\nabla_x f=(f^S-f)/\tau$
  - (2) $f^S=f^{eq}\left[1+(1-\Pr)\dfrac{\boldsymbol c\cdot\boldsymbol q}{5pRT}\left(\dfrac{|\boldsymbol c|^2}{RT}-5\right)\right]$、$\boldsymbol c=\boldsymbol\xi-\boldsymbol u$
  - (3) $f^{eq}=\rho(2\pi RT)^{-3/2}\exp(-|\boldsymbol c|^2/2RT)$
- モーメント (4)–(6): $\boldsymbol U=(\rho,\rho\boldsymbol u,\rho E)=\int\boldsymbol\psi f\,d\boldsymbol\xi$、$\boldsymbol\psi=(1,\boldsymbol\xi,|\boldsymbol\xi|^2/2)$、$E=\tfrac32RT+\tfrac12|\boldsymbol u|^2$、$p=\rho RT$、$\boldsymbol\Pi=\int\boldsymbol c\boldsymbol c f=p\boldsymbol I+\boldsymbol\sigma$、$\boldsymbol q=\int\tfrac12|\boldsymbol c|^2\boldsymbol c f$
- 厳密なマクロ保存式 (7)(構成近似なし)
- (8) $\mathrm{Kn}=\dfrac{\mu}{pL_{ref}}\sqrt{\dfrac{\pi RT}{2}}$
- NSF (9): $\boldsymbol\sigma^{NSF}=-\mu[\nabla\boldsymbol u+(\nabla\boldsymbol u)^T-\tfrac23(\nabla\cdot\boldsymbol u)\boldsymbol I]$、$\boldsymbol q^{NSF}=-\kappa\nabla T$
- (10) $\mu=p\tau$、$\kappa=\tfrac52\mu R/\Pr$ (11) $\mu=\mu_{ref}(T/T_{ref})^\omega$

## 2. 反復の全体構造(§3.1, Fig.1)

```
初期化(ケースにより NSF のみのマクロ反復 + 運動論反復で初期場を作る)
for n = 0, 1, 2, ...                                   # GSIS 反復
  1. CIS: f^n → f^{n+1/2}(運動論反復を N_CIS 回, Eq.15)
  2. f^{n+1/2} のモーメント → W^{n+1/2}=(p,u,T), σ, q。境界の半空間フラックス F_b^{±,m=0} を保存 (Eq.35)
  3. HoT_σ = σ − σ^NSF(W^{n+1/2}),  HoT_q = q − q^NSF(W^{n+1/2})   (Eq.12, セル中心勾配で評価)
  4. PBC: W^0 = W^{n+1/2}
       for m = 0 .. M−1:
         a. 内部面フラックス (Eq.18–32)・境界フラックス (Eq.36–38) → 残差 R (Eq.39)
         b. (J^pu + M_pu) ΔW_pu = −R^pu を解いて p, u を更新(T 固定)
         c. 面量を再評価し、(J_T + M_T) ΔT = −R_T を解いて T を更新(p, u 固定)
         d. W^{m+1} が収束 または m+1 = M なら終了 → W^{n+1}
  5. 分布関数の補正 (Eq.14): f^{n+1} = f^{n+1/2} − f^eq(W^{n+1/2}) + f^eq(W^{n+1})
     セル i で min_k f < −1e−8·ρ_i/(R T_i)^{3/2} なら、そのセルの全速度点を f^eq(W_i^{n+1}) で置換
  6. 収束判定 (Eq.59)
```

- 合成方程式 (13) は「NSF 項(現在の W で更新)+ HoT(PBC 中は固定)」の定常マクロ方程式。収束時はモーメント方程式 (7) と一致する。

## 3. CIS(運動論ソルバ, §3.2)

- (15) $\dfrac{V_i}{\Delta t}(f^{\ell+1}_{i,k}-f^{\ell}_{i,k})+\sum_{j}(\boldsymbol\xi_k\cdot\boldsymbol S_{ij})f^{\ell+1}_{ij,k}=\dfrac{V_i}{\tau^\ell_i}(f^{S,\ell}_{i,k}-f^{\ell+1}_{i,k})$
  - 面値 $f_{ij,k}$ は $\boldsymbol\xi_k\cdot\boldsymbol S_{ij}$ の符号で選ぶ風上側セルからの線形再構成。勾配に Venkatakrishnan リミッタ $\varphi\in[0,1]$
  - $\Delta t$ は安定化のための擬似時間刻み(値は記載なし → 仮定 A2)
- 境界面 (16)(17): 流出側 $\Xi_b^+$ は隣接セルからの再構成値、流入側 $\Xi_b^-$ は境界条件 $f^{bc}_{b,k}$(§5)
- 速度空間: 一様デカルト格子、積分は重み $\varpi_k$ の和

## 4. PBC マクロソルバ(§3.3)

### 4.1 内部面フラックス (18)–(32)
- Rhie–Chow 体積流量 (21): $\dot{\mathcal V}^{RC}_{ij}=\boldsymbol u_{ij}\cdot\boldsymbol S_{ij}-\beta^{RC}_{ij}\Delta_{ij}p$、$\boldsymbol u_{ij}=w_i\boldsymbol u_i+w_j\boldsymbol u_j$
- (22)(23) $\boldsymbol d_{ij}=\boldsymbol x_j-\boldsymbol x_i$、$d_{n,ij}=\boldsymbol n_{ij}\cdot\boldsymbol d_{ij}$、$w_j=\min\{1,\max[0,\boldsymbol n_{ij}\cdot(\boldsymbol x_{ij}-\boldsymbol x_i)/d_{n,ij}]\}$、$w_i=1-w_j$
- (24) $\Delta_{ij}p=(p_j-p_i)-\overline{\nabla p}_{ij}\cdot\boldsymbol d_{ij}$、$\overline{\nabla p}_{ij}=w_i(\nabla p)_i+w_j(\nabla p)_j$
- (25)(26) $\beta^{RC}_{ij}=\dfrac{A_{ij}}{d_{n,ij}}(w_iD_i+w_jD_j)$、$D_i=V_i/a_{u,i}$、
  $a_{u,i}=\sum_{j\in\mathcal N^{int}_i}\left[\max(\widehat{\dot m}_{ij},0)+\dfrac{4\mu_{ij}A_{ij}}{3d_{n,ij}}\right]+\sum_{b}\|\boldsymbol B_{u,ib}\|_2$
  - $\widehat{\dot m}_{ij}$: 直近に評価した面質量流量(初回は $(\rho_i+\rho_j)[(\boldsymbol u_i+\boldsymbol u_j)\cdot\boldsymbol n_{ij}]A_{ij}/4$)、$\mu_{ij}$ は算術平均
  - $\boldsymbol B_{u,ib}$: 境界ヤコビアン (49) の運動量×速度 3×3 ブロック。$\|\cdot\|_2$ は最大特異値
- (27)(28) ドナーセル風上: $\dot m_{ij}=\rho_{ij}\dot{\mathcal V}^{RC}_{ij}$、$\rho_{ij}=p_{up}/(RT_{up})$、
  $\boldsymbol F^{adv}_{ij}=[\dot m_{ij},\ \dot m_{ij}\boldsymbol u_{up}+p_{ij}\boldsymbol S_{ij},\ \dot m_{ij}H_{up}]$、$p_{ij}=w_ip_i+w_jp_j$、$H=\tfrac52RT+\tfrac12|\boldsymbol u|^2$
  - 高次化は任意: $(p,\boldsymbol u,T)$ を勾配制限付きで面に再構成し、風上状態から $\rho_{ij},\boldsymbol u_{up},H_{up}$ を評価(仮定 A11)
- (29) 非直交補正付き面勾配(速度成分・温度、セル勾配は最小二乗):
  $\nabla\phi_{ij}=\overline{\nabla\phi}_{ij}+\dfrac{(\phi_j-\phi_i)-\overline{\nabla\phi}_{ij}\cdot\boldsymbol d_{ij}}{d_{n,ij}}\boldsymbol n_{ij}$、$\kappa_{ij}$ は算術平均
- (30) HoT の面値は同じ重み $w$ で補間
- (31)(32) $\boldsymbol t_{ij}=[\boldsymbol\sigma^{NSF}_{ij}+\mathrm{HoT}_{\sigma,ij}]\cdot\boldsymbol S_{ij}$、$Q_{ij}=[\boldsymbol q^{NSF}_{ij}+\mathrm{HoT}_{q,ij}]\cdot\boldsymbol S_{ij}$、
  $\boldsymbol F^{vis}_{ij}=[0,\ \boldsymbol t_{ij},\ \boldsymbol t_{ij}\cdot\overline{\boldsymbol u}_{ij}+Q_{ij}]$、$\overline{\boldsymbol u}_{ij}=(\boldsymbol u_i+\boldsymbol u_j)/2$

### 4.2 メソ–マクロ整合境界フラックス (33)–(39)
- (33)(34) Maxwell 分布の半空間フラックス $\boldsymbol F^\pm_{eq}(\boldsymbol W;\boldsymbol n_b)$(閉形式は付録 B.1 (68)–(74))、$\widetilde{\boldsymbol F}^+_b=A_b\boldsymbol F^+_{eq}$
- (35) PBC 開始時に離散運動論フラックスを保存: $\boldsymbol F^{\pm,m=0}_b=A_b\sum_{\Xi^\pm_b}\varpi_k(\boldsymbol\xi_k\cdot\boldsymbol n_b)\boldsymbol\psi(\boldsymbol\xi_k)f^{n+1/2}_{b,k}$
- (36) $\boldsymbol F^{+,m}_b=\widetilde{\boldsymbol F}^+_b(\boldsymbol W^m_{i,b})+[\boldsymbol F^{+,0}_b-\widetilde{\boldsymbol F}^+_b(\boldsymbol W^0_{i,b})]$(括弧内は固定の整合補正)
- (37) $\boldsymbol F^{-,m}_b=\widetilde{\boldsymbol F}^-_b(\boldsymbol W^m_{i,b},\boldsymbol F^{+,m}_b)+[\boldsymbol F^{-,0}_b-\widetilde{\boldsymbol F}^-_b(\boldsymbol W^0_{i,b},\boldsymbol F^{+,0}_b)]$
- (38) $\boldsymbol F^m_b=\boldsymbol F^{+,m}_b+\boldsymbol F^{-,m}_b$、(39) $\boldsymbol{\mathcal R}_i=\sum_{int}(\boldsymbol F^{adv}+\boldsymbol F^{vis})+\sum_b\boldsymbol F_b$
- $\boldsymbol W_{i,b}$ は (15) と同じ制限付き再構成で境界面へ外挿。不動点では運動論の離散境界フラックスと厳密に一致する。

### 4.3 圧力–速度連成 (40)–(49)
- 残差は (39) の質量・運動量成分。非線形残差は選んだ再構成、近似ヤコビアンは 1 次精度のセル中心フラックスから作る
- (44) $\partial\dot m/\partial p_i=\rho_{ij}\beta+\delta_{i,up}\dot{\mathcal V}/(RT_i)$、$\partial\dot m/\partial p_j=-\rho_{ij}\beta+\delta_{j,up}\dot{\mathcal V}/(RT_j)$、$\partial\dot m/\partial\boldsymbol u_{i(j)}=\rho_{ij}w_{i(j)}\boldsymbol S^T$(風上セルは固定)
- (45) $\boldsymbol C_{\mu,ij}=\dfrac{\mu_{ij}A_{ij}}{d_{n,ij}}(\boldsymbol I_3+\tfrac13\boldsymbol n\boldsymbol n^T)$(非直交部・輸送係数の微分・HoT は微分しない)
- (47) $\boldsymbol J_{mom,ij,s}=\boldsymbol u_{up}\boldsymbol J_{\rho,ij,s}+\dot m\,\delta_{s,up}\boldsymbol E_u+w_s\boldsymbol S\boldsymbol e_p^T+\zeta_s\boldsymbol C_\mu\boldsymbol E_u$($\zeta_i=1,\zeta_j=-1$)
- (48)(49) 境界ブロック(4×4): $\left[(\boldsymbol I_5+\partial\widetilde{\boldsymbol F}^-/\partial\boldsymbol F^+)\,\partial\widetilde{\boldsymbol F}^+/\partial\boldsymbol W_{pu}+\partial\widetilde{\boldsymbol F}^-/\partial\boldsymbol W_{pu}\right]_{\rho,mom}$(整合補正は固定、$\boldsymbol W_{i,b}=\boldsymbol W_i$ で評価。詳細は付録 C)

### 4.4 温度方程式 (50)–(55)
- (51) $\mathcal R_{T,i}=\mathcal R_{E,i}-\boldsymbol u_i\cdot\boldsymbol{\mathcal R}_{mom,i}+(K_i-\tfrac32RT_i)\mathcal R_{\rho,i}-\sum_{int}\mathcal K_{i,ij}$、$K_i=\tfrac12|\boldsymbol u_i|^2$、$\mathcal K_{i,ij}=\tfrac12\dot m_{ij}|\boldsymbol u_{up}-\boldsymbol u_i|^2$
- (52) $F_{T,ij}=\dot m(\tfrac52RT_{up}-\tfrac32RT_i)-p_{ij}\boldsymbol u_i\cdot\boldsymbol S+\boldsymbol t\cdot(\overline{\boldsymbol u}-\boldsymbol u_i)+Q$、境界は $F_{E,b}-\boldsymbol u_i\cdot\boldsymbol F_{mom,b}+(K_i-\tfrac32RT_i)F_{\rho,b}$
- (54) $\boldsymbol J^{int}_{T,ij}=R\dot m\cdot\{[1,0]\ (\dot m\ge0),\ [-\tfrac32,\tfrac52]\ (\dot m<0)\}+C_{\kappa,ij}[1,-1]$、$C_\kappa=\kappa_{ij}A_{ij}/d_{n,ij}$
- (55) 境界: $\partial F_{E,b}/\partial T_i-\boldsymbol u_i\cdot\partial\boldsymbol F_{mom,b}/\partial T_i+(K_i-\tfrac32RT_i)\partial F_{\rho,b}/\partial T_i-\tfrac32RF_{\rho,b}$

### 4.5 擬似時間安定化 (56)–(58)
- $\Delta t^{pse}_i=\mathrm{CFL}\,V_i/\Lambda_i$、$\Lambda_i=\sum_jA_{ij}\left[|\boldsymbol u_{ij}\cdot\boldsymbol n_{ij}|+\chi_{a,ij}c_{s,ij}+\dfrac{8\mu_{ij}}{3\rho_id_{n,ij}}\right]$
- $\chi_a=\min[1,\max(0.01,U_{ij}/c_{s,ij})]$、$U_{ij}=\sqrt{(|\boldsymbol u_i|^2+|\boldsymbol u_j|^2)/2}$、$c_s=\sqrt{\gamma RT}$($\gamma=5/3$)。境界面は内側セルの値、$d_n=2|\boldsymbol x_{ij}-\boldsymbol x_i|$
- (57) $\boldsymbol M_{pu,i}=\dfrac{V_i}{\Delta t_i}\mathrm{diag}(0,\rho_i\boldsymbol I_3)$、$M_{T,i}=\dfrac{3\rho_iRV_i}{2\Delta t_i}$(連続式には擬似時間項なし)
- (58) $(\boldsymbol J^{pu}+\boldsymbol M_{pu})\Delta\boldsymbol W_{pu}=-\boldsymbol{\mathcal R}^{pu}$、$(\boldsymbol J_T+\boldsymbol M_T)\Delta\boldsymbol T=-\boldsymbol{\mathcal R}_T$。線形ソルバは「標準的な反復法」とのみ記載
- CFL = 500(全ケース)

## 5. 境界条件(付録 A, B, C)

| 境界 | 運動論: 流入側 $f^{bc}$(付録 A) | マクロ: $\widetilde{\boldsymbol F}^-_b$(付録 B) | ヤコビアン |
|---|---|---|---|
| 質量流量規定 | $\rho_{\Gamma,b}f^{eq}(\boldsymbol W^{(1)}_\Gamma)$、$\rho_{\Gamma,b}=(-j_\Gamma-j^+_b)/j^{-,(1)}_{\Gamma,b}$(離散速度空間で面ごとの流量を厳密に満たす)(61)–(63) | $A_b\tilde\rho\,\boldsymbol F^-_{eq}(\boldsymbol W^{(1)}_\Gamma)$、$\tilde\rho=\dfrac{j_\Gamma A_b+(\boldsymbol F^+_b)_\rho}{A_b\sqrt{RT_\Gamma/2\pi}}$ (75) | (81) |
| 静止完全拡散壁 | 上で $j_\Gamma=0$、$T_\Gamma=T_w$ | 同左 | 同左 |
| 圧力出口 | $f^{eq}(\boldsymbol W^{out}_b)$、$\boldsymbol W^{out}_b=(p_{out},\boldsymbol u_{i,b}+\Delta u\,\boldsymbol n_b,T_{i,b})$、$\Delta u=\dfrac{p_{i,b}-p_{out}}{\rho_{i,b}c_{s,i,b}}$ (64)(66) | $A_b\boldsymbol F^-_{eq}(\boldsymbol W^{out}_b)$ (76) | (82)–(84) |
| 圧力入口 | $f^{eq}(\boldsymbol W^{in}_b)$、$\boldsymbol W^{in}_b=(p_{in},\boldsymbol u_{i,b}+\Delta u\,\boldsymbol n_b,T_{in})$、$\Delta u=\dfrac{p_{i,b}-p_{in}}{\rho_{i,b}c_{s,i,b}}$ (65)(66) | $A_b\boldsymbol F^-_{eq}(\boldsymbol W^{in}_b)$ (76) | (82)–(84) |
| 遠方場 | $f^{eq}(\boldsymbol W_\infty)$ (67) | $A_b\boldsymbol F^-_{eq}(\boldsymbol W_\infty)$(固定)(77) | (85) |
| 真空(仮定 A6) | 0 | 0 | $A_b\boldsymbol J^+_{eq}$ |

- $\boldsymbol W^{(1)}_\Gamma=(RT_\Gamma,\boldsymbol 0,T_\Gamma)$(単位密度・静止の Maxwell 分布)、$\boldsymbol n_b$ は流体領域から外向き
- 半空間モーメントの閉形式 (70)(72): $\eta=u_n/(\sqrt2a)$、$a=\sqrt{RT}$、$\mathcal C_\pm=\tfrac12[1\pm\mathrm{erf}\,\eta]$、$\mathcal G=e^{-\eta^2}/\sqrt{2\pi}$、
  $I_1^\pm=u_n\mathcal C_\pm\pm a\mathcal G$、$I_2^\pm=(u_n^2+a^2)\mathcal C_\pm\pm u_na\mathcal G$、$I_3^\pm=(u_n^3+3u_na^2)\mathcal C_\pm\pm a(u_n^2+2a^2)\mathcal G$、
  $\boldsymbol F^\pm_{eq,loc}=\rho[I_1,\ I_2,\ u_{t1}I_1,\ u_{t2}I_1,\ \tfrac12(I_3+(u_{t1}^2+u_{t2}^2+2a^2)I_1)]^T$、全体座標へは (73)(74) の $\boldsymbol P$ で回転
- 半空間ヤコビアンは (79)(80)

## 6. 収束判定・共通設定(§4 冒頭)

- (59) $\epsilon=\max_{Q\in\{\rho,\boldsymbol u,T\}}\left[\dfrac{\sum_i|Q^n_i-Q^{n-1}_i|^2V_i}{\sum_i|Q^{n-1}_i|^2V_i}\right]^{1/2}<10^{-5}$(連続する GSIS 反復間)
- 報告されている壁時計時間は初期化を含む。GSIS 反復あたりのコストはマクロ線形ソルバの反復数で変わる
- GSIS-PBC の全計算は 3 次元メッシュ(軸対称形状は検証の都合で選んだだけ)。DSMC は SPARTA の 2 次元軸対称・VHS

## 7. テストケース諸元(§4)

### 7.1 円管 → 真空(§4.1)
| 項目 | 値 |
|---|---|
| 形状(管半径 $R=L_{ref}$ で無次元) | 上流槽 半径 8・長さ 8 / 管 半径 1・長さ 10 / 下流槽 半径 4・長さ 4(同軸)(Fig.2) |
| 境界 | 上流槽の端面: 圧力入口($p_{in}$, $T_0$) / 下流槽の端面: 真空 / その他: 静止完全拡散壁($T_0$) |
| ガス | He: $m=6.65\times10^{-27}$ kg、$\mu_0=1.916\times10^{-5}$ Pa·s @ $T_0=273.15$ K、$\omega=0.66$ |
| 条件 | $\delta_{rp}=\sqrt\pi/(2\mathrm{Kn})=1, 10, 100, 1000$(Kn は入口状態・管半径基準: $8.86\times10^{-1}$〜$8.86\times10^{-4}$)。$p_{in}=\delta_{rp}\mu_0\sqrt{2RT_0}/L_{ref}$ |
| 格子 | 空間 135,000 セル(4 ケース共通)、速度 30×24×24 一様、$\boldsymbol\xi/\sqrt{RT_0}\in[-10,10]\times[-8,8]^2$ |
| 反復 | GSIS 1 回 = CIS 5 回 + PBC 10 回。CIS 単独も同じ格子・収束判定で比較 |
| 出力 | 無次元質量流量 $\dot M=\dot m/(\sqrt\pi L_{ref}^2p_{in}/\sqrt{2RT_0})$、中心線上の $p/p_{in}$ と $u_x/\sqrt{2RT_0}$(Fig.3, X=0〜10) |
| 計算機 | Intel Xeon Silver 4208、96 コア |

Table 1(参照値は δ=1,10,100 が DSMC、δ=1000 が FLUENT の NS):

| $\delta_{rp}$ | 参照 $\dot M$ | GSIS-PBC $\dot M$ | 誤差 % | 反復 | 時間 h | CIS $\dot M$ | 誤差 % | 反復 | 時間 h | 速度比 |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 0.197 | 0.198 | 0.51 | 17 | 0.20 | 0.198 | 0.51 | 189 | 0.43 | 2.2× |
| 10 | 0.330 | 0.326 | 1.21 | 9 | 0.11 | 0.348 | 5.45 | 484 | 1.05 | 9.5× |
| 100 | 0.898 | 0.885 | 1.45 | 7 | 0.09 | 0.597 | 33.52 | 2,796 | 6.20 | 68.9× |
| 1000 | 1.267 | 1.275 | 0.63 | 6 | 0.08 | 0.729 | 42.46 | 7,847 | 16.03 | 200.4× |

- DSMC(2D 軸対称): 0.225 / 0.899 / 4.2 M セル、平均 44 粒子/セル、640 コアで 0.55 / 2.19 / 10.2 h
- δ=100 で最大セルは入口平均自由行程の約 57 倍(CIS の数値散逸の原因)

### 7.2 動的ガスロック DGL(§4.2)
| 項目 | 値 |
|---|---|
| 形状(Fig.4b, mm。図からの読み取りを含む) | 同軸の 2 室(各 Ø200×高さ 100)、室間 20。中央通路はテーパ(上端 R7.5、下端 R3.5、線形と読める)。上下の板厚 8.5、その間に高さ 3 の環状入口流路(r=100 が入口)。下室にウェハ Ø100×厚さ 2(下室天井から 10 下、壁)。子午面半断面を 360° 回転 |
| 境界 | 環状入口: 質量流量規定 10 / 100 / 1000 sccm、300 K / 上面出口 7 Pa / 下面出口 1 Pa / その他(ウェハ含む)壁 300 K |
| 格子 | 空間 850,080 セル、速度 24×24×24、$\boldsymbol\xi/\sqrt{RT_0}\in[-8,8]^3$ |
| 反復 | GSIS-PBC = CIS 10 + PBC 10。比較用 GSIS-DB = CIS 2 + 密度ベースマクロ 400 |
| 計算機 | AMD EPYC 7452、640 コア |

Table 2(上部出口の正味質量流量 [sccm]、正 = 流出。参照は 10・100 sccm が DSMC、1000 sccm が Fluent):

| 入口 sccm | 参照 | GSIS-PBC | 誤差 % | 反復 | 時間 h |
|---|---|---|---|---|---|
| 10 | −18.73 | −18.66 | 0.37 | 16 | 0.35 |
| 100 | 57.45 | 56.73 | 1.25 | 20 | 0.44 |
| 1000 | 762.70 | 768.22 | 0.72 | 9 | 0.21 |

- GSIS-DB は 10・100 sccm とも 2,818 反復以上・13.23 h(速度比 約 60× / 30×)
  - 注: 13.23 h ÷ 0.35 h = 37.8 で、10 sccm の「約 60 倍」と合わない(論文側の不整合)。13.23 h が両条件に共通の下限なので、10 sccm の GSIS-DB は実際には約 21 h かかったと読める
- 図: Fig.5 速度分布(Z=0 面、Y=95 の水平線上の $U=u_x/\sqrt{2RT_0}$、X=0 の軸上の $V$)、Fig.6 圧力と流線
- DSMC: 2D 軸対称 225,000 セル、12 M / 18 M 粒子、4.12 / 12.55 h

### 7.3 核融合ダイバータ(§4.3)
| 項目 | 値 |
|---|---|
| 形状 | ITER の 3D プリント用簡易トカマクモデルのダイバータ部品から作成(全周、Fig.7a)。断面図 Fig.7b(X 2500〜5000 mm、Z 3800〜6500 mm)。配布 ZIP(`https://static.iter.org/com/3d/IO_3D_models_v2.0.zip`, 6.4 GB)内に `Models/Divertor/Divertor_triple_casette.{stp (981 MB), stl (25.5 MB), obj (104 MB)}` |
| 境界 | 圧力入口 I1・I2: 10 Pa、420 K / 漏れ口 O1〜O4: 真空 / その他: 壁 420 K(ポンプ確率 0 なので排気面も壁) |
| 条件 | Kn = 0.298($L_{ref}$ = 1 cm、入口状態) |
| 格子 | 多面体 12,248,344 セル・80,495,318 面(境界層付き, Fig.7c)、速度 10,648 点(= 22³、範囲の記載なし) |
| 反復 | 初期化: マクロ 50 + 運動論 5。以降 CIS 5 + PBC 10。11 反復・0.34 h(3,200 コア) |
| 結果 | 入口 I1 6.338e−4、I2 1.237e−3 kg/s / 出口 O1 1.143e−4、O2 3.945e−4、O3 9.522e−4、O4 4.097e−4 kg/s(収支一致)。大部分で Ma ~ 1e−2 |
| 比較 | GSIS-DB は 23.7 h 以上。DSMC(DTT の 10.8° セクター、別論文)6.9e5 core-h に対し、同じ周方向範囲に換算して約 33 core-h |

### 7.4 球まわり流れ(§4.4)
| 項目 | 値 |
|---|---|
| 形状 | 球 D = 1 m($L_{ref}$)、同心の球面遠方場境界 半径 5 m |
| ガス | Ar: $m=6.63\times10^{-26}$ kg、$\mu_{ref}=2.117\times10^{-5}$ Pa·s @ 273.15 K、$\omega=0.81$ |
| 条件 | $T_\infty=273.15$ K、$p_\infty=0.1266$ Pa(Kn = 0.05、D 基準)、+x 方向、Ma = 2 / 5 / 10(615.8 / 1,539.5 / 3,079.0 m/s)、壁 $T_w=T_\infty$ 完全拡散 |
| 格子 | 360,000 セル(構造 O 型、第 1 層 0.005 m)、速度 30×24×24(Ma 2, 5)/ 40×36×36(Ma 10)。範囲の記載なし |
| 反復 | 初期化: マクロ 100 + 運動論 5。以降 PBC 10 / マクロ更新、CIS 6(Ma 2, 5)/ 10(Ma 10) |

Table 3:

| Ma | GSIS-PBC コア | 反復 | 時間 h | DSMC セル | DSMC 粒子 | DSMC コア | 時間 h |
|---|---|---|---|---|---|---|---|
| 2 | 288 | 8 | 0.13 | 100,356 | 3,709,747 | 200 | 0.27 |
| 5 | 288 | 10 | 0.18 | 100,356 | 3,710,463 | 200 | 0.39 |
| 10 | 480 | 10 | 0.32 | 182,649 | 7,156,819 | 200 | 1.15 |

- 図: Fig.10(z=0 面の $u_x$ と $T$ の等値線、DSMC との比較)。Ma 5・10 では衝撃波上流の温度上昇が DSMC より早い(Shakhov の衝突頻度が速度に依存しないため、と考察)

## 8. 論文に明記がない点と仮定案

| # | 項目 | 論文の記述 | 仮定案(仕様で確定する) |
|---|---|---|---|
| A1 | CIS の陰的解法 | (15) は $f^{\ell+1}$ について陰的。解法の記載なし | 左辺は 1 次風上、右辺は 2 次の残差(defect correction)とし、LU-SGS(前進+後退スイープ)を 1 往復 / 反復 |
| A2 | CIS の擬似時間刻み $\Delta t$ | 「安定化のため」のみ | 局所 CFL で決める(パラメータ化)。$\Delta t=\infty$ も選べるようにする |
| A3 | $f$ の勾配の計算法 | 記載なし(マクロ側は最小二乗) | 最小二乗に統一 |
| A4 | Venkatakrishnan リミッタの定数 K | 記載なし | パラメータ化(既定値は要検討) |
| A5 | 速度格子の点配置・重み | 「一様デカルト」のみ。球・ダイバータは範囲も記載なし | 区間中点配置 + 中点則を既定。範囲はケースごとに決める(球 Ma 10 は淀み点付近の高温を覆う広い範囲が必要) |
| A6 | 真空境界 | 付録 A に定義なし | 流入分布 0(= $p_{out}=0$ の圧力出口と等価) |
| A7 | 境界隣接セルの勾配・HoT 評価に使う境界面値 | 記載なし | 境界面分布 $f_b$ のモーメントを境界値として使う |
| A8 | PBC 内側の収束判定、線形ソルバの種類と許容誤差 | 「標準的な反復法」のみ。M = 10 | Krylov(GMRES / BiCGStab)+ ILU / AMG。許容誤差と内側収束判定はパラメータ化 |
| A9 | 初期条件 | 管・DGL は記載なし | 一様状態から NSF のみの PBC 反復で初期化し、平衡分布から開始 |
| A10 | GSIS-DB(比較用の密度ベース GSIS) | 文献 [25, 27] の手法 | 当面スコープ外。比較は CIS 単独で行う |
| A11 | PBC 移流項の高次再構成 | 使ってもよい("may") | 1 次風上を既定、2 次はオプション |
| A12 | sccm の標準状態 | 記載なし | 273.15 K、101,325 Pa(1 sccm(He)≈ 2.98e−9 kg/s) |
| A13 | DGL・ダイバータの寸法 | 図のみ | DGL は図から寸法を確定。ダイバータは ITER モデルから作り、近似再現と位置づける |
| A14 | メッシュの種類 | ダイバータは多面体、球は構造 O 型(図)。管・DGL は記載なし | 管・DGL・球は六面体主体、ダイバータは多面体(テトラの双対変換など)。ソルバは任意多面体に対応させる |
| A15 | DSMC 参照データ | 非公開(Data will be made available on request) | 表の数値と図のデジタイズで比較。必要なら著者に請求 |

### 決定(フェーズ 3 の実装、2026-10-16)

| # | 決定 |
|---|---|
| A7 | (注、2026-10-17: δ=1000 では、運動論の境界面のモーメントを使うと 1 反復遅れて戻る経路ができ、上流槽で格子規模のモードが 1 反復 2〜2.5 倍で育った。δ=1000 のケースはセルの値にする。`docs/phase5b_d1000_report.md`)GSIS の段では、境界セルの勾配の境界値に運動論の境界面分布のモーメント(`face_state`)を使う(論文どおり)。NSF のみの単独モード(初期化)では、境界面の状態にセルの値を使う。論文どおりの制限付き再構成は、一様な状態から始めると発散するか停滞したため |
| A8 | 線形ソルバーは AMGCL 1.5.0 の GMRES(30) + ILU0(既定)。CPR は管 δ=1000 で時間を半分にするが、δ=100 で解けないことがあるので選択肢にとどめる。PBC 内側の収束判定は体積重みの相対更新量(速度は $\sqrt{\lvert\boldsymbol u\rvert^2+T}$ で規格化) |
| A9 | 初期化は、一様な状態から NSF のみの単独モードで行う。論文の連続式には擬似時間項がないので、CFL は 5 から残差に応じて 500 まで上げる(SER)。反復数の既定は 50 |
| A11 | 移流項は 1 次と 2 次を実装済み。フェーズ 5c(2026-10-17)で既定を 2 次にした(初期化だけ 1 次)。論文に書かれた離散化(セル中心の HoT と 1 次の移流項)では、GSIS の不動点が運動論の解からずれ、δ=10 で出口 0.328・入口 0.344 と質量が保存しない(`docs/phase5b_d10_report.md`) |
| — | GSIS の段の境界面の再構成は論文どおり制限付き(`"limited"`)を既定とし、フェーズ 4 で不安定なら `"cell"` にする(GSIS の不動点はこの選び方によらない) |
| — | 管の質量流量の比較(フェーズ 5)は GSIS の結果だけで行う。単独モードの値は初期化の目安にとどめる(壁際のセルが粗いと過小評価する。四面体の管で −14%) |

## 9. 計算規模の見積り(分布関数 $f$ を 1 本だけ保持する場合)

| ケース | セル数 × 速度点数 | 未知数 | FP64 | FP32 |
|---|---|---|---|---|
| 管(全 4 ケース) | 135,000 × 17,280 | 2.33e9 | 18.7 GB | 9.3 GB |
| DGL | 850,080 × 13,824 | 1.18e10 | 94.0 GB | 47.0 GB |
| ダイバータ | 12,248,344 × 10,648 | 1.30e11 | 1.04 TB | 522 GB |
| 球 Ma 2, 5 | 360,000 × 17,280 | 6.22e9 | 49.8 GB | 24.9 GB |
| 球 Ma 10 | 360,000 × 51,840 | 1.87e10 | 149 GB | 74.6 GB |

- 参考: 作業 PC は Ryzen 7 7700(8 コア / 16 スレッド)、RAM 31.3 GB、RTX 5070 Ti(VRAM 16 GB)。論文と同規模で載るのは管だけ。
- 対称面(速度格子の軸と揃った鏡映面)を使えば、DGL・球は 1/4 領域で計算でき、未知数も 1/4 になる(論文にはない拡張)。
  - DGL 1/4: 23.5 GB(FP64)/ 11.8 GB(FP32)
  - 球 Ma 2, 5 の 1/4: 12.4 GB / 6.2 GB
  - 球 Ma 10 の 1/4: 37.3 GB / 18.7 GB

## 付録: 形状ツールの評価(2026-10-15)

usd-brep 1.0.0(NVIDIA, Apache-2.0, PyPI に Windows 用ホイールあり、Python 3.12 専用)と gmsh 4.15.2(OpenCASCADE カーネル)で、論文の計算領域を作って比較した。

| 項目 | usd-brep | gmsh |
|---|---|---|
| 管の領域(円柱 3 つの和) | 正確(体積誤差 8e−7、面 7 枚が境界パッチに対応) | 正確。入口・出口・壁を Physical Group で付与 |
| DGL(子午面断面の 360° 回転、軸上にウェハの切り欠き) | 体積が B-rep で +0.25%、テッセレーションで +7.4% ずれる(切り欠き付き円柱だけの単純な断面でも +0.5% / +15%)。エラーは出ず `is_manifold_solid()` も True。ウェハを別の円柱にしてブーリアン差で抜けば正確 | 正確(体積誤差なし) |
| 球の領域(球の差) | 正確 | 正確 |
| 体積メッシュ生成 | なし(表面の三角形分割のみ) | あり(管: 29 万テトラを 3.6 s、DGL: 14 万テトラを 1.7 s。六面体・境界層も可) |
| STEP / IGES 読み込み | なし(USD と OpenCASCADE の `.brep` のみ。後者は C++ ツール経由) | あり(STEP / IGES / BREP / STL) |
| CFD 結果の可視化 | 対象外 | 対象外(ParaView / PyVista を使う) |
