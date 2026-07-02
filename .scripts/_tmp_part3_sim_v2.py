"""
Ad-hoc, read-only simulation for Part 3 of the conviction-score sector-weighting review.
Stdlib only. Not production code. Not executed as part of any pipeline.
"""
import math

# ---------------------------------------------------------------------------
# 1. Weight rescaling arithmetic (verify, don't trust)
# ---------------------------------------------------------------------------
OLD_WEIGHTS = {
    "who": 0.30,
    "buy_size": 0.30,
    "company_size": 0.22,
    "earnings_timing": 0.18,
}
assert abs(sum(OLD_WEIGHTS.values()) - 1.0) < 1e-9

SECTOR_WEIGHT = 0.20
RESCALE = 1.0 - SECTOR_WEIGHT  # 0.80

NEW_WEIGHTS = {k: round(v * RESCALE, 6) for k, v in OLD_WEIGHTS.items()}
NEW_WEIGHTS["sector"] = SECTOR_WEIGHT

print("=== Weight rescale check ===")
for k, v in OLD_WEIGHTS.items():
    print(f"  {k:16s} old={v:.4f}  x0.80 = {v*0.80:.4f}")
print(f"  sector           new=0.2000 (fixed)")
total_new = sum(NEW_WEIGHTS.values())
print(f"  SUM new weights = {total_new:.6f}  (expect 1.000000)")
assert abs(total_new - 1.0) < 1e-9, "weights do not sum to 1.0"
print()
print("New weight table:")
for k, v in NEW_WEIGHTS.items():
    print(f"  {k:16s} {v:.4f}  ({v*100:.1f}%)")
print()

# ---------------------------------------------------------------------------
# 2. Sector momentum calibration data (trailing-30-day net BUY-SELL count,
#    pulled live from Supabase, window ending 2026-07-02, same box-car input
#    the old F6 mechanism used before Parts 1/2 replaced it)
# ---------------------------------------------------------------------------
SECTOR_NET_30D = {
    "Financials": 24,
    "Industrials": 15,
    "Consumer Discretionary": 14,
    "Energy": 9,
    "Materials": 8,
    "Consumer Staples": 6,
    "Real Estate": 6,
    "Health Care": 4,
    "Technology": 2,
    "Communication Services": -3,
    "Utilities": -3,
    None: 0,   # untagged ticker -> neutral treatment
}

vals = [v for k, v in SECTOR_NET_30D.items() if k is not None]
n = len(vals)
mean_v = sum(vals) / n
var_v = sum((v - mean_v) ** 2 for v in vals) / n
sd_v = math.sqrt(var_v)
sorted_vals = sorted(vals)
median_v = sorted_vals[n // 2] if n % 2 else (sorted_vals[n // 2 - 1] + sorted_vals[n // 2]) / 2
print("=== Observed trailing-30-day net-buy-count distribution (11 real sectors) ===")
print(f"  values sorted: {sorted_vals}")
print(f"  n={n} mean={mean_v:.2f} median={median_v:.1f} sd={sd_v:.2f} min={min(vals)} max={max(vals)}")
print()

# ---------------------------------------------------------------------------
# 3. Sigmoid mapping net-buy-count -> [0,1] strength subscore
#    subscore = 1 / (1 + exp(-net / STEEPNESS))
#    Centered at net=0 -> 0.5 (neutral). STEEPNESS calibrated so the
#    observed spread maps to a sensible (not saturated, not flat) range.
# ---------------------------------------------------------------------------
def sigmoid_subscore(net, steepness):
    return 1.0 / (1.0 + math.exp(-net / steepness))

print("=== Sigmoid calibration sweep (steepness constant k) ===")
for k in (5, 8, 10, 12, 15, 20):
    lo = sigmoid_subscore(min(vals), k)
    hi = sigmoid_subscore(max(vals), k)
    at_mean = sigmoid_subscore(mean_v, k)
    print(f"  k={k:3d}: min(net={min(vals):+d})->{lo:.3f}   max(net={max(vals):+d})->{hi:.3f}   mean(net={mean_v:+.1f})->{at_mean:.3f}")
print()

# Chosen steepness: k=12. Rationale printed in report; verified below that this
# keeps the coldest observed sector (-3) meaningfully below 0.5 and the hottest
# (24) close to but not pinned at 1.0, preserving headroom.
K_CHOSEN = 12.0
print(f"CHOSEN steepness k={K_CHOSEN}")
for sector, net in sorted(SECTOR_NET_30D.items(), key=lambda kv: (kv[1] is None, kv[1] if kv[1] is not None else 0)):
    sub = sigmoid_subscore(net if net is not None else 0, K_CHOSEN)
    print(f"  {str(sector):24s} net_30d={net!s:>4}  subscore={sub:.4f}  points(x20)={sub*20:.2f}")
print()

# ---------------------------------------------------------------------------
# 4. Load the N=147 cohort (fingerprint, rank_old, score_old, f1,f2,f3,f4,
#    f6_mult_old, ticker, director, role, sector)
# ---------------------------------------------------------------------------
RAW = """2639c159c7b86ca3|1|100|1|0.674156502487618|1|0|2|TOO|Scott Livingston|Chief Executive Officer|Financials
f38c7a357b58018d|2|100|0.2|1|0.361894718939479|0.757142857142857|2|N91|Ninety One Guernsey Employee Benefit Trust||Financials
181bb6a11fb469de|3|100|0.9|0.0349378206605812|0.892407830807206|0.856|1.75|SHI|Simon Kesterton|PDMR (Chief Financial Officer)|Industrials
fae65c85a9178836|4|100|0.2|0.709526533259804|0.979721188089555|0.1|2|RVRB|Christopher Mills|PDMR|Financials
5ad401dbd5bfe4b9|5|100|0.9|0.111403257333748|0.201551731135844|0.952|2|HSX|Paul Cooper|PDMR - Chief Financial Officer, Hiscox Group|Financials
bd6aaaf667a907a3|6|100|1|0.0377312586845273|0.92242847494927|0.871428571428571|1.75|MPAC|Adam Holland|CEO|Industrials
e4f88a281956b922|7|100|0.7|0.0384622652141567|1|0|2|KRM|Keith Todd|Executive Director|Financials
a6e58491b8441582|8|100|0.9|0|1|0|1.75|SNX|Paul Williams|Chief Financial Officer|Industrials
74371fe9d31e9e78|9|100|1|0.133409383346224|0.184521618154055|0.988|2|IGG|Breon Corcoran|Chief Executive Officer|Financials
9e8a0adfb66c6e83|10|100|1|0.252780840063292|0.86932698837106|0.1|1.75|ECEL|William Truman|Chief Executive Officer|Industrials
0e6efe17e3875112|11|100|1|1|0.926214535786287|0.1|1.5|IGR|Anders Hedlund|Founder and Non-Executive Director|Consumer Discretionary
79249f8ca823ed18|12|100|0.9|0.0362083767243074|0.92242847494927|0.642857142857143|1.75|MPAC|Duncan Tyler|Interim Chief Financial Officer|Industrials
4a2007d4022da614|13|100|1|0.1333322794358|0.64662233428077|1|1.75|LUCE|Judith Hoy|PCA of Will Hoy, CFO|Industrials
9110afbef3755d42|14|100|1|0.133028351740902|0.491129190708484|0|2|GROW|Ben Wilkinson|Chief Executive Officer|Financials
0d33bd799e5a745d|15|100|0.9|0.266796164584234|1|0|2|KLSO|Ian Selby|Chief Financial Officer (PDMR)|Financials
bd1039907b7de077|16|100|1|0.183187083153156|0.1|0.808|2|PRU|Douglas Flint|Chair of the Board (PDMR)|Financials
d12e2c3f31022f2d|17|100|1|0.260779226928426|1|0|1.75|HERC|Mrs Paula Wheatcroft|PCA of Paul Wheatcroft, Chief Financial Officer|Industrials
c1a4599079566642|18|99.7747934301026|1|0.183979890501711|0.1|0.676|2|PRU|Douglas Flint|Chair of the Board|Financials
c1122f5df0d57ba4|19|99.5865698914806|0.3|0.501642831524676|1|0.208|2|EGT|Michael Kearney|Non-Executive Director|Financials
a9b49ddaf8ed878e|20|99.2241925965098|0.9|0.198105301842593|0.968154525643071|0|1.5|G4M|Chris Scott|Chief Financial Officer|Consumer Discretionary
8f04448d13b27133|21|97.9073123360959|1|0.175188710321833|0.357971559589603|0.712|1.75|HILS|Nick Anderson|Chair|Industrials
1421a34a817c6f9a|22|97.030026185792|0.2|0.363343886685475|0.697395295105989|0.904|2|MAB1|Mortgage Advice Bureau||Financials
449704ccd48973b1|23|96.4921502081256|1|0.436933529359539|1|0.671428571428571|1.25|PXEN|Tom Reynolds|CEO|Energy
8ccc4aa046742d8b|24|93.4259190959097|0.9|0.409580097563893|1|0|1.25|MLVN|Daniel Fisher|Chief Financial Officer|Consumer Staples
a2a4500fdda3123e|25|91.1340142827414|0.7|0.33026698406092|1|0.436|1.5|TRT|Ryan Maughan|Managing Director|Consumer Discretionary
7449a8fd65a8aa0f|26|90.5562876476958|1|0.340701003938554|1|0.568|1.25|PXEN|Tom Reynolds|CEO|Energy
b6662538f75defd1|27|89.1517926112886|0.3|0.12239604973883|1|0.904|1.75|STAF|Catherine Lynch|Non-Executive Director|Industrials
df367c5deb8545d1|28|88.4840969304279|1|0.227089272210676|0.926214535786287|0.1|1.5|IGR|Stewart Gilliland|Interim Executive Chair|Consumer Discretionary
9c76ef6b14b19bdc|29|88.2900379950248|0.3|0.575507359020452|1|0|1.5|B90|Andrew McIver|Non-Executive Chairman|Consumer Discretionary
3db32d677f5b46d5|30|88.0267528254395|0.3|0.177395362664621|0.92242847494927|0.871428571428571|1.75|MPAC|Clive Whiley|NED|Industrials
80d7656739c919dd|31|87.44|1|0|0.1|0.64|2|PRU|Anil Wadhwani|Chief Executive Officer (PDMR)|Financials
845117377d757644|32|87.44|1|0|0.1|0.64|2|PRU|Rajeev Mittal|Chief Executive Officer, Eastspring Investments (PDMR)|Financials
4522ceda934c32f5|33|86.8806558392292|1|0.203674811037644|0.73901274273882|0.952|1.25|TLW|Birgitte Plauborg|Person Closely Associated to Roald Goethe, Chair of Tullow Oil plc|Energy
5f9e45d3fcfa6b55|34|86.0545895824436|0.9|0|0.758623926740715|0.76|1.5|EVOK|Sean Wilkins|Chief Financial Officer|Consumer Discretionary
93fd4b77676dfd44|35|83.5088488089461|0.3|0.762418862421024|1|0.1|1.5|TND|Simon Bragg|Non-Executive Director|Consumer Discretionary
159934f5d7d99f62|36|83.5065661518889|0.9|0.216477477132031|0.926214535786287|0.1|1.5|IGR|Rohan Cummings|Chief Financial Officer|Consumer Discretionary
9925f4d3795c3fd5|37|82.0618918293513|0.3|0.0637789627391308|0.92242847494927|0.871428571428571|1.75|MPAC|Simon Kesterton|NED|Industrials
4c92272adc5d39a8|38|81.44|0.9|0|0.1|0.64|2|PRU|Ben Bulmer|Chief Financial Officer (PDMR)|Financials
00b31476862c49de|39|79.9888927602515|1|0.225033579705288|0.420004855320118|1|1.25|GNC|LESLIE VAN DE WALLE AND Domitille marie Renée van de walle|NON-EXECUTIVE CHAIR AND PERSON CLOSELY ASSOCIATED|Consumer Staples
efc200b43670d8fb|40|77.7583334855704|0.9|0|0.310858590471829|1|1.5|INCH|Adrian Lewis|PDMR - Group Chief Financial Officer|Consumer Discretionary
2d63c641ebe04952|41|77.2049023724748|0.3|0.212874933175506|0.487765412487291|1|1.75|ITM|Sir Warren East|Non-Executive Director|Industrials
4b496571c2c2802e|42|76.8226093012016|0.7|0.570620310040054|1|0.928|1|ALT|Martin Varley|Chief Strategy Officer|Technology
7fa007ab196a6e31|43|75.9703848088884|1|0|0.678923083959577|0.88|1.25|MSLH|Simon Bourne|Chief Executive Officer|Materials
9bd3ca47620666a1|44|75.8486287659844|0.3|0.464236279037079|0.842583122164808|0|1.5|TUNE|Ian Barkshire|Non-Executive Director and Chair Designate|Consumer Discretionary
1ad9f09535fb812f|45|75.4161255523934|0.3|0.0824934714260393|0.374239028791615|1|2|RAT|Iain Cummings|Non-Executive Director|Financials
cf7ee71caeb2eec0|46|74.8919258013799|1|0.703064193379328|1|0.1|1|TST|Lynden Jones|Chief Executive Officer|Technology
5a16cca323694687|47|74.8483675360477|0.7|0.850412251201592|1|0.352|1|EMAN|Charles Dorfman|Interim Creative Director and Executive Director|Communication Services
5e0577e8097eb54c|48|74.5964059672666|0.3|0.104615639017717|0.354896991504627|0.964|2|UKW|Caoimhe Giblin|Non-executive Director of Greencoat UK Wind Plc|Financials
8867a0eada9cc5a5|49|74.5818680209783|0.3|0.092730890198693|0.92242847494927|0.585714285714286|1.75|MPAC|David Squires|Non-Executive Director|Industrials
b50729fd5246f408|50|74.5104765529632|1|0.18749968734491|0.556757291589157|0.1|1.5|DOCS|Ije Nwokorie|PDMR (Chief Executive Officer)|Consumer Discretionary
4b066b7aa22d6542|51|74.2488054035645|0.3|0.0369215933689593|0.924289200654212|0|2|AWEM|Howard Pearce|Director|Financials
74d875ada0442721|52|74.1713621951964|0.3|0.207213754701275|0.455964677230636|0.952|1.75|CGEO|Neil Janin|PDMR - Non-Executive Director|Industrials
94e1cb85cc2c51a0|53|73.7798839270796|0.3|0.0552227776708083|0.374239028791615|1|2|RAT|Iain Cummings|Non-Executive Director|Financials
ad72cb3783962f1f|54|73.5829346909461|1|0.118401470477022|0.669016411043179|0|1.25|MTVW|Mrs Corrine Sinclair, Wife of Duncan Morrall Sinclair|Person Closely Associated to the Chief Executive Officer|Real Estate
cb33743cbe4559f8|55|73.0997895895606|0.3|0.0741574830169404|0.648546702142237|0.614285714285714|2|JAGI|George William Edwards Rogers|Director|Financials
49c6b2f289330769|56|73.0916346355992|0.2|0|0.737173514445437|0.796|2|STB|Andrew Phillips|PDMR|Financials
4dbfb92e0fee435c|57|72.9894743846601|1|0.261712299847376|1|0|1|RHR|Hamish Harris|CEO|NULL
ee5c1bcbf639131b|58|72.2203848088884|0.9|0|0.678923083959577|0.88|1.25|MSLH|Justin Lockwood|Chief Financial Officer|Materials
b9c4e63a83f89131|59|72.1297455099195|0.3|0.0277204707181402|0.374239028791615|1|2|RAT|Terri Duhon|Non-Executive Director|Financials
242dbfe0375345f3|60|71.2286625059463|1|0.0394565418969945|0.757056079447602|0.508|1.25|LSL|Adam Castleton|Group Chief Executive Officer|Real Estate
91b4c81ea670441e|61|70.7094784708615|1|0.263798974507511|0.672523147074372|1|1|PAY|Nick Wiles|Chief Executive|Technology
d6d33966b4e9c990|62|70.0069105909462|0.3|0|0.591088586777823|1|1.75|HAS|Joseph Hurd|Non-Executive Director, PDMR|Industrials
8e7a02760d0a0c92|63|69.5530773454093|0.3|0.139542780915491|0.361894718939479|0.757142857142857|2|N91|Charles Harman|Director|Financials
68653699620a7085|64|69.44|0.7|0|0.1|0.64|2|PRU|Kenneth Rappold|Chief Strategy and Transformation Officer (PDMR)|Financials
9b12b63dd882c98b|65|69.44|0.7|0|0.1|0.64|2|PRU|Catherine Chia|Chief Human Resources Officer (PDMR)|Financials
d9a8ec9d7f4bdd38|66|69.44|0.7|0|0.1|0.64|2|PRU|Avnish Kalra|Chief Risk and Compliance Officer (PDMR)|Financials
167cfd2102f4c83c|67|68.95328368145|0.3|0|0.602994381336364|0.952|1.75|COST|Amanda Lucia Fisher|NON-EXECUTIVE DIRECTOR (PDMR)|Industrials
ab54e1d985a5a012|68|68.7610417021724|0.7|0.282755312593586|0.705371544724106|0.556|1.25|SOHO|Steven Windsor|Director of Investment Manager|Real Estate
0d53a71de7f2391c|69|68.2203701274373|1|0.217876536731662|0.1|0.88|1.25|BATS|Luciana Franco Do Amaral|CEO|Consumer Staples
0e936fced5ab7b58|70|68.2203701274373|1|0.217876536731662|0.1|0.88|1.25|BATS|Tadeu Marroco|Chief Executive|Consumer Staples
f448a7a4d55f585f|71|68.1840277177645|0.2|0.0888912077296114|0.876759778745232|0|2|AFL|William Tamworth|Portfolio Manager|Financials
6d5e72b9d1753d5a|72|68.1742409012523|0.3|0.0429040150208712|1|0.1|2|ONWD|Luke Allen|PDMR / Non-Executive Director|Financials
db052e6cfbd1be73|73|68.1701652758745|0.7|0.613405509195816|1|0.376|1|EMAN|Charles Dorfman|Interim Creative Director and Executive Director|Communication Services
8ada67cf56a0746c|74|68.0379225297467|1|0.474597417658224|1|0.1|1|TST|Lynden Jones|Chief Executive Officer|Technology
08432dad21272612|75|67.9037642024537|0.7|0.251725473415123|1|0.964|1|ALT|Martin Varley|Chief Strategy Officer|Technology
2aac87ff4c8cbe16|76|66.7861195630883|1|0.0921539347244148|1|0|1|DGQ|Richard Michael Jones|Chief Executive Officer|NULL
71247e1deeb3f576|77|65.5484261698146|1|0.0746246978617236|0.1|1|1.25|AAL|Stuart Chambers|Chair (Director/PDMR)|Materials
a417cf52fcefe05b|78|65.4890250800655|0.7|0.00514638803256915|0.150460040866167|0.46|2|MNG|Chris Cochrane|Chief Information Technology Officer|Financials
f56b0c7ab772792f|79|65.232269069981|1|0.457627198453613|0.758156959835119|0.268|1|TTG|Philip Swash|Chair|Technology
7eb1fae974e0441a|80|65.1289023058103|0.2|0.133011922063765|0.42918899733669|0.988|1.75|CKN|Constantin Cotzias|The business to capitalise on opportunities in its markets|Industrials
f0ded38bdbaaf7bc|81|64.1194327553032|0.3|0|0.32816892625689|0.88|2|QLT|Chris Samuel|Non-executive Director, Quilter plc - PDMR|Financials
7f3966c35ceccc99|82|63.6409092035822|0.3|0.170156903562153|0.193705760170627|1|1.75|MRO|Chris Grigg|Non-executive Chair|Industrials
919af57a72032d38|83|62.3029772056571|1|0.132753675470587|0.621055640199846|0.814285714285714|1|GBG|Dev Dhiman|CEO (Chief Executive Officer)|Technology
52e91ccf3509bb28|84|61.6559198761041|0.3|0.161147583038761|0.193705760170627|0.952|1.75|MRO|Guy Hachey|Non-executive Director|Industrials
be545ff3ce8db6e8|85|61.3063430210491|0.3|0|0.365099818728548|1|1.75|MGNS|Mark Robson|Non-executive Director|Industrials
7909a8bcf3b1862a|86|61.002118270387|0.3|0.0433686378397835|0.1|1|2|LGEN|Mark Jordy|Non-Executive Director|Financials
5ec600cd4eac0955|87|60.1003575817084|1|0.0157428688455573|0.1|0.856|1.25|DGE|Sir John Manzoni|Chair|Consumer Staples
fdb6486861300e3d|88|59.9589511987784|0.3|0|0.184521618154055|0.94|2|IGG|Susan Skerritt|Non-Executive Director|Financials
e5cd9e41ccb33555|89|59.9019314913969|1|0.00185200804379182|0.420004855320118|0|1.25|SRE|Sharon Clarke-Wills|PCA to Andrew Coombs, CEO|Real Estate
abc7421252f6c898|90|59.896440636417|0.3|0.45356595084011|0.705371544724106|0.544|1.25|SOHO|Jonathan Short|Independent Non-Executive Chair|Real Estate
bb531fa7a8a1095e|91|59.6580235702226|0.2|0.377313913275456|0.991647548354651|0|1.25|AIC|Philip David Cooper|PDMR / Director|Real Estate
b83040fc0b68dd8f|92|59.602532395946|0.3|0.316334227362894|0.724727231630454|0.736|1.25|CAPD|Anu Dhir|Non-Executive Director|Materials
fc51969985ba7d34|93|58.9584981766669|1|0.329816605888897|0.5|0.448|1|V3TC|Charlie Wood|Executive Chairman|NULL
7811d670c47c51fe|94|58.6380927575504|0.3|0.0351830235820298|1|0|1.5|CTA|Geraint Davies|Senior Independent Non-Executive Director|Consumer Discretionary
bf2f3bc0cc94b977|95|58.1275137559814|0.3|0.0673488063650769|0.582355293136367|0|2|SYNC|John Patrick Roche|Non-Executive Director|Financials
3bfe8b16c9d01d72|96|58.0142663388749|0.3|0.14830442436153|0.73901274273882|0.928|1.25|TLW|Garrett Soden|Non-Executive Director|Energy
2fe71d8bb758b914|97|58.0127161056214|0.9|0.0897449721360645|0.621055640199846|0.814285714285714|1|GBG|David Ward|CFO (Chief Financial Officer)|Technology
55e85037878ab797|98|57.5939819560519|0.2|0|0.405038492364986|1|1.75|KLR|Peter Wyton|PDMR|Industrials
cb38388b493b56b8|99|57.2626758099916|0.3|0.437920314159676|0.287296873781954|0.964|1.25|SEPL|Christopher Okeke|Independent Non-Executive Director|Energy
265ba132d8603240|100|55.7197487063384|0.3|0.703459926831379|0.964725040972594|0.244|1|AOTI|Richard Cotton|Senior Independent Director|Health Care
b22dad1a5ac7e6fd|101|55.356|0.3|0|1|0.328|1.5|AIEA|Tanya Ashton|Independent Non-Executive Director|Consumer Discretionary
82cb253fd7d2da97|102|55.1805104904799|0.2|0|0.361894718939479|0.757142857142857|2|N91|Ninety One Guernsey Employee Benefit Trust||Financials
f6b6ca5110aed254|103|54.841563837136|0.3|0.332668496587811|0.5|0|1.5|LIVE|Fionnuala Hogan|Independent Non-Executive Director|Consumer Discretionary
d58146f787cf8571|104|53.7388927602515|0.3|0.225033579705288|0.420004855320118|1|1.25|GNC|Leslie Van de Walle|Non-Executive Chair|Consumer Staples
843d1f99ca5d8385|105|51.9758018709511|0.3|0.46578970868506|0.734497738009505|0.136|1.25|PANR|Michael Spencer|Non-executive Chairman|Energy
3959dc8e24f50633|106|50.8968264342226|0.3|0|0.665836964414151|0.1|2|TAM|Pippa Hamnett|Non-Executive Director|Financials
e48aa4bee160d9e9|107|50.7621086486613|0.3|0.146295630555057|0.880601806000436|1|1|BOOM|Michael Tobin|Non-Executive Chairman|Communication Services
91e83dfc4c4ef454|108|50.3773666664208|0.3|0.0802730441216297|0.926214535786287|0.1|1.5|IGR|Clare Askem|Senior Independent Director|Consumer Discretionary
69456c43eac653f4|109|49.8643374644585|0.3|0|0.323040529226017|0.952|1.5|TW|Mark Castle|Independent Non Executive Director|Consumer Discretionary
f6d951acbc54fe08|110|48.7140451647271|0.3|0.462639369551699|0.775742543114786|0|1|UTL|Peter Durhager|PDMR (Non-executive Director)|Utilities
cb96bc6b80272074|111|48.4167135059405|0.3|0.369992648640547|0.920587911214729|0.448|1|VLG|Paul McGreevy|Non-Executive Chairman|Health Care
31d87cc2650927b2|112|47.9551150070917|0.3|0.0264452294698724|0.926214535786287|0.1|1.5|IGR|John Gittins|Non-Executive Director|Consumer Discretionary
6fce06bab4a8b71d|113|47.5224831734362|0.3|0.303834543417995|0.734497738009505|0.208|1.25|PANR|David Wilkins|Non-Executive Director|Energy
fc5fe0cc769b1f95|114|46.6850895854279|0.3|0.120737886111041|0.668997049318685|0.556|1.25|AT|Jean Cahuzac|Independent Non-Executive Director|Energy
c7013e259b5da744|115|45.9392591496778|0.5|0.10374272854622|0.441414793788892|0.496|1.25|ENOG|Ciaran Boyle|Group General Counsel|Energy
22e6cb5d10603ea7|116|45.6511437348872|0.3|0.378920063802742|0.775742543114786|0|1|UTL|Peter Durhager|PDMR (Non-executive Director)|Utilities
fe0574122b37909a|117|45.5785936527422|0.3|0.37693702822411|0.775742543114786|0|1|UTL|Stuart Bridges|PDMR (Non-executive Director)|Utilities
d492bd9217ef65fd|118|45.4373934374923|0.3|0.0401460179934589|0.891024979781416|0|1.25|IOF|Tim Hughes|Non-Executive Director|Materials
c0a1dd68a2f24450|119|45.2233447441421|0.3|0.367226891389042|0.775742543114786|0|1|UTL|Peter Durhager|PDMR (Non-executive Director)|Utilities
3ec5634e1f2e4d6c|120|45.0116449184504|0.3|0.19698496110431|1|0|1|GANA|Farzad Peyman|Director|NULL
c7e8d5eca1a236ac|121|44.6872544060438|0.3|0|0.584263796583412|0.772|1.25|AEP|Farah Suhanah Tun Ahmad Sarji|Senior Independent Non-Executive Director|Consumer Staples
4a0d6d40e8ca3c47|122|43.2484200506669|0.3|0.313245616434052|0.775742543114786|0|1|UTL|David Shillson|PDMR (Non-executive Director)|Utilities
3a5e20655f913f1d|123|42.658147607355|0.3|0.0401258662456533|0.740488277205198|0.424|1.25|CAML|Alison Baker|Non-Executive Director|Materials
657629d783a8b590|124|41.7898291862271|0.2|0.213218723573292|0.440151849315045|0.1|1.75|CHG|Tony Wood|Director/PDMR|Industrials
c70c876767e43df1|125|41.5181771679694|0.3|0.101496842591162|1|0|1|DGQ|Mark Burnett|Non-Executive Chairman|NULL
b0e221bc18908a10|126|41.4845893912994|0.3|0.467245443362184|0.5|0|1|TAR|Marcus Yeoman|Non-Executive Chairman|NULL
506fc70c3de917ae|127|40.696152442696|0.3|0.299840613199065|0.920587911214729|0.136|1|VLG|Paul McGreevy|Non-Executive Chairman|Health Care
3deee472fb83c88a|128|40.6517685248689|0.2|0.0401296886677952|0.64662233428077|0.1|1.75|LUCE|Janet Ryan|PDMR|Industrials
4d45ae5510a125d4|129|39.4558561806532|0.3|0.0451267356045219|1|0|1|OMG|Ian Wilcock|Non-Executive Director|Technology
f94554706e8781cc|130|39.44|0.2|0|0.1|0.64|2|PRU|Dennis Tan||Financials
d0cd24451588ad1c|131|37.9282713161576|0.3|0|0.658432464125989|0.1|1.5|FSTA|Sir James Fuller|Non-Executive Director|Consumer Discretionary
15a2441785c82bf4|132|37.5026793809352|0.3|0.0267381168249374|0.1|1|1.25|AAL|Magali Anderson|Non-Executive Director (Director/PDMR)|Materials
7098c09ca40181e2|133|37.4346587945372|0.2|0.130263097064913|0.440151849315045|0.1|1.75|CHG|Alpna Amar|Director/PDMR|Industrials
bfae21050b0d1751|134|37.2997900133926|0.3|0.0213277336904692|0.1|1|1.25|AAL|Nonkululeko Nyembezi|Non-Executive Director (Director/PDMR)|Materials
4c222bc66bb4fa02|135|36.3103645357997|0.3|0.258292657824756|0.1|0.1|1.75|RR.|Angela Strank|Non-Executive Director|Industrials
b520b301f968ca8b|136|34.9886923599282|0.3|0|0.569354313937218|0.1|1.5|SMWH|Simon Emeny|Non-Executive Director|Consumer Discretionary
5f2ddeb042e1dff4|137|34.659824096247|0.3|0.490928642566587|0.1|0.1|1.25|KYGA|Ms Fiona Dawson|Non-Executive Director (PDMR)|Consumer Staples
1f5fe676a0b7c277|138|34.2160440394895|0.3|0.133309007733613|0.421126082158232|0.664|1|GNS|Celia Baxter|Non-Executive Director|Health Care
1fd7f349526aa98d|139|33.3224888896595|0.2|0.0656663703909188|0.1|0.916|1.25|BATS|Yulia Wheaton||Consumer Staples
c88f1a1435db714f|140|32.4399977708128|0.3|0.0405713861107201|0.1|0.34|1.75|HLMA|Sharmila Nebhrajani OBE|Independent non-executive Director|Industrials
168c4093444b0221|141|28.3802411247519|0.3|0.174616827688539|0.384804003142969|0|1.25|BME|Peter Pritchard|Non-Executive Director|Consumer Staples
4472cd3489b55bfb|142|27.8807252290749|0.3|0.0396728644742512|0.57600039789154|0|1|TEP|Gemma Godfrey|Non-executive Director|Utilities
98ec4d1670c0529c|143|22.75|0.3|0|0.1|0.1|1.75|RR.|Birgit Behrendt|Non-Executive Director|Industrials
bff80c45e0831def|144|22.75|0.3|0|0.1|0.1|1.75|RR.|Wendy Mars|Non-Executive Director|Industrials
48c1dc9aaee17a3d|145|22.75|0.3|0|0.1|0.1|1.75|RR.|Birgit Behrendt|Non-Executive Director|Industrials
67ed8311b385124f|146|22.75|0.3|0|0.1|0.1|1.75|RR.|Wendy Mars|Non-Executive Director|Industrials
976ef1e9eac0be58|147|22.75|0.3|0|0.1|0.1|1.75|RR.|Beverly Goulet|Non-Executive Director|Industrials"""

rows = []
for line in RAW.strip().split("\n"):
    parts = line.split("|")
    fp, rank_old, score_old, f1, f2, f3, f4, f6mult, ticker, director, role, sector = parts
    rows.append({
        "fp": fp,
        "rank_old": int(rank_old),
        "score_old": float(score_old),
        "f1": float(f1),
        "f2": float(f2),
        "f3": float(f3),
        "f4": float(f4),
        "f6mult_old": float(f6mult),
        "ticker": ticker,
        "director": director,
        "role": role,
        "sector": None if sector == "NULL" else sector,
    })

assert len(rows) == 147, f"expected 147 rows, got {len(rows)}"
print(f"Loaded {len(rows)} rows OK")
print()

# ---------------------------------------------------------------------------
# 4b. has_earnings flag per fingerprint, pulled live: weights_used ? 'earnings_timing'
#     31/147 rows use a RENORMALIZED 3-key weight set (who=0.36585, buy_size=0.36585,
#     company_size=0.26829 -- i.e. 0.30/0.30/0.22 rescaled to sum to 1.0 after DROPPING
#     earnings_timing entirely) rather than the flat 4-key 0.30/0.30/0.22/0.18 set.
#     Must mirror this behaviour in both the OLD-score reconstruction and the NEW
#     5-factor score, or the simulation silently misrepresents ~21% of the cohort.
# ---------------------------------------------------------------------------
HAS_EARNINGS_BLOB = "2639c159c7b86ca3|0,f38c7a357b58018d|1,181bb6a11fb469de|1,fae65c85a9178836|1,5ad401dbd5bfe4b9|1,bd6aaaf667a907a3|1,e4f88a281956b922|0,a6e58491b8441582|0,74371fe9d31e9e78|1,9e8a0adfb66c6e83|1,0e6efe17e3875112|1,79249f8ca823ed18|1,4a2007d4022da614|1,9110afbef3755d42|0,0d33bd799e5a745d|0,bd1039907b7de077|1,d12e2c3f31022f2d|0,c1a4599079566642|1,c1122f5df0d57ba4|1,a9b49ddaf8ed878e|0,8f04448d13b27133|1,1421a34a817c6f9a|1,449704ccd48973b1|1,8ccc4aa046742d8b|0,a2a4500fdda3123e|1,7449a8fd65a8aa0f|1,b6662538f75defd1|1,df367c5deb8545d1|1,9c76ef6b14b19bdc|0,3db32d677f5b46d5|1,80d7656739c919dd|1,845117377d757644|1,4522ceda934c32f5|1,5f9e45d3fcfa6b55|1,93fd4b77676dfd44|1,159934f5d7d99f62|1,9925f4d3795c3fd5|1,4c92272adc5d39a8|1,00b31476862c49de|1,efc200b43670d8fb|1,2d63c641ebe04952|1,4b496571c2c2802e|1,7fa007ab196a6e31|1,9bd3ca47620666a1|0,1ad9f09535fb812f|1,cf7ee71caeb2eec0|1,5a16cca323694687|1,5e0577e8097eb54c|1,8867a0eada9cc5a5|1,b50729fd5246f408|1,4b066b7aa22d6542|0,74d875ada0442721|1,94e1cb85cc2c51a0|1,ad72cb3783962f1f|0,cb33743cbe4559f8|1,49c6b2f289330769|1,4dbfb92e0fee435c|0,ee5c1bcbf639131b|1,b9c4e63a83f89131|1,242dbfe0375345f3|1,91b4c81ea670441e|1,d6d33966b4e9c990|1,8e7a02760d0a0c92|1,68653699620a7085|1,9b12b63dd882c98b|1,d9a8ec9d7f4bdd38|1,167cfd2102f4c83c|1,ab54e1d985a5a012|1,0d53a71de7f2391c|1,0e936fced5ab7b58|1,f448a7a4d55f585f|0,6d5e72b9d1753d5a|1,db052e6cfbd1be73|1,8ada67cf56a0746c|1,08432dad21272612|1,2aac87ff4c8cbe16|0,71247e1deeb3f576|1,a417cf52fcefe05b|1,f56b0c7ab772792f|1,7eb1fae974e0441a|1,f0ded38bdbaaf7bc|1,7f3966c35ceccc99|1,919af57a72032d38|1,52e91ccf3509bb28|1,be545ff3ce8db6e8|1,7909a8bcf3b1862a|1,5ec600cd4eac0955|1,fdb6486861300e3d|1,e5cd9e41ccb33555|0,abc7421252f6c898|1,bb531fa7a8a1095e|0,b83040fc0b68dd8f|1,fc51969985ba7d34|1,7811d670c47c51fe|0,bf2f3bc0cc94b977|0,3bfe8b16c9d01d72|1,2fe71d8bb758b914|1,55e85037878ab797|1,cb38388b493b56b8|1,265ba132d8603240|1,b22dad1a5ac7e6fd|1,82cb253fd7d2da97|1,f6b6ca5110aed254|0,d58146f787cf8571|1,843d1f99ca5d8385|1,3959dc8e24f50633|1,e48aa4bee160d9e9|1,91e83dfc4c4ef454|1,69456c43eac653f4|1,f6d951acbc54fe08|0,cb96bc6b80272074|1,31d87cc2650927b2|1,6fce06bab4a8b71d|1,fc5fe0cc769b1f95|1,c7013e259b5da744|1,22e6cb5d10603ea7|0,fe0574122b37909a|0,d492bd9217ef65fd|0,c0a1dd68a2f24450|0,3ec5634e1f2e4d6c|0,c7e8d5eca1a236ac|1,4a0d6d40e8ca3c47|0,3a5e20655f913f1d|1,657629d783a8b590|1,c70c876767e43df1|0,b0e221bc18908a10|0,506fc70c3de917ae|1,3deee472fb83c88a|1,4d45ae5510a125d4|0,f94554706e8781cc|1,d0cd24451588ad1c|1,15a2441785c82bf4|1,7098c09ca40181e2|1,bfae21050b0d1751|1,4c222bc66bb4fa02|1,b520b301f968ca8b|1,5f2ddeb042e1dff4|1,1f5fe676a0b7c277|1,1fd7f349526aa98d|1,c88f1a1435db714f|1,168c4093444b0221|1,4472cd3489b55bfb|0,98ec4d1670c0529c|1,bff80c45e0831def|1,48c1dc9aaee17a3d|1,67ed8311b385124f|1,976ef1e9eac0be58|1"

has_earnings_map = {}
for pair in HAS_EARNINGS_BLOB.split(","):
    fp, flag = pair.split("|")
    has_earnings_map[fp] = (flag == "1")

for r in rows:
    r["has_earnings"] = has_earnings_map[r["fp"]]

n_dropped = sum(1 for r in rows if not r["has_earnings"])
print(f"Rows with earnings_timing DROPPED (renormalized 3-key weights): {n_dropped}/147")
print()

# ---------------------------------------------------------------------------
# 5. Reconstruct OLD live score to verify fidelity of the pulled data.
#    IMPORTANT: must use the CORRECT per-row weight set. When earnings_timing
#    is dropped (has_earnings=False), the live pipeline renormalizes who/buy_size/
#    company_size (0.30/0.30/0.22 -> /0.82 -> 0.36585/0.36585/0.26829) and f4
#    contributes ZERO -- it is not simply "treat f4=0 under the flat 4-key weights".
# ---------------------------------------------------------------------------
def clamp01(x):
    return max(0.0, min(1.0, x))

OLD_W3_RENORM = {  # who/buy_size/company_size renormalized to sum 1.0 (drop earnings)
    "who": OLD_WEIGHTS["who"] / (1 - OLD_WEIGHTS["earnings_timing"]),
    "buy_size": OLD_WEIGHTS["buy_size"] / (1 - OLD_WEIGHTS["earnings_timing"]),
    "company_size": OLD_WEIGHTS["company_size"] / (1 - OLD_WEIGHTS["earnings_timing"]),
}
print("OLD_W3_RENORM check vs live weights_used (who/buy_size/company_size):",
      f"{OLD_W3_RENORM['who']:.8f} / {OLD_W3_RENORM['buy_size']:.8f} / {OLD_W3_RENORM['company_size']:.8f}",
      "(live: 0.36585365853658536 / 0.36585365853658536 / 0.2682926829268293)")

max_resid = 0.0
worst_row = None
for r in rows:
    if r["has_earnings"]:
        ws = (OLD_WEIGHTS["who"] * r["f1"] + OLD_WEIGHTS["buy_size"] * r["f2"] +
              OLD_WEIGHTS["company_size"] * r["f3"] + OLD_WEIGHTS["earnings_timing"] * r["f4"])
    else:
        ws = (OLD_W3_RENORM["who"] * r["f1"] + OLD_W3_RENORM["buy_size"] * r["f2"] +
              OLD_W3_RENORM["company_size"] * r["f3"])  # f4 excluded entirely
    recon = 100 * clamp01(ws) * r["f6mult_old"]
    recon = min(recon, 100.0)  # score is clamped to 100 downstream in live pipeline
    resid = abs(recon - r["score_old"])
    if resid > max_resid:
        max_resid = resid
        worst_row = r["ticker"]
print(f"Reconstruction check (old mechanism, correct per-row weights): max residual = {max_resid:.3f} points (worst: {worst_row})")
print()

# ---------------------------------------------------------------------------
# 6. Compute score_new under the 5-factor design
#    score_new = 100 * clamp01(w1.f1+w2.f2+w3.f3+w4.f4+w5.sector_subscore)
#
#    When earnings_timing is dropped (has_earnings=False), mirror the SAME
#    renormalization behaviour the live pipeline uses today: rescale the
#    remaining weights (who/buy_size/company_size/sector) proportionally so
#    they sum to 1.0.
# ---------------------------------------------------------------------------
NEW_W4_RENORM = {  # who/buy_size/company_size/sector renormalized to sum 1.0 (drop earnings)
    k: v / (1 - NEW_WEIGHTS["earnings_timing"])
    for k, v in NEW_WEIGHTS.items() if k != "earnings_timing"
}
_chk = sum(NEW_W4_RENORM.values())
print(f"NEW_W4_RENORM (earnings dropped) sums to {_chk:.6f} (expect 1.0):",
      {k: round(v, 4) for k, v in NEW_W4_RENORM.items()})
print()

for r in rows:
    net = SECTOR_NET_30D.get(r["sector"], 0)
    sub = sigmoid_subscore(net, K_CHOSEN)
    r["sector_net30d"] = net
    r["sector_subscore"] = sub

    if r["has_earnings"]:
        ws_new = (NEW_WEIGHTS["who"] * r["f1"] + NEW_WEIGHTS["buy_size"] * r["f2"] +
                  NEW_WEIGHTS["company_size"] * r["f3"] + NEW_WEIGHTS["earnings_timing"] * r["f4"] +
                  NEW_WEIGHTS["sector"] * sub)
        r["sector_points"] = sub * NEW_WEIGHTS["sector"] * 100
    else:
        ws_new = (NEW_W4_RENORM["who"] * r["f1"] + NEW_W4_RENORM["buy_size"] * r["f2"] +
                  NEW_W4_RENORM["company_size"] * r["f3"] + NEW_W4_RENORM["sector"] * sub)
        r["sector_points"] = sub * NEW_W4_RENORM["sector"] * 100
    r["score_new"] = 100 * clamp01(ws_new)

# rank_new: 1 = highest score_new
sorted_new = sorted(rows, key=lambda r: -r["score_new"])
for i, r in enumerate(sorted_new, start=1):
    r["rank_new"] = i

# ---------------------------------------------------------------------------
# 7. Ceiling clustering old vs new
# ---------------------------------------------------------------------------
n_ceil_old = sum(1 for r in rows if r["score_old"] >= 99.999)
n_95_old = sum(1 for r in rows if r["score_old"] >= 95.0)
n_ceil_new = sum(1 for r in rows if r["score_new"] >= 99.999)
n_95_new = sum(1 for r in rows if r["score_new"] >= 95.0)
print("=== Ceiling clustering: old vs new ===")
print(f"  OLD: at 100.0 = {n_ceil_old}/147 ({n_ceil_old/147*100:.1f}%)   >=95 = {n_95_old}/147 ({n_95_old/147*100:.1f}%)")
print(f"  NEW: at 100.0 = {n_ceil_new}/147 ({n_ceil_new/147*100:.1f}%)   >=95 = {n_95_new}/147 ({n_95_new/147*100:.1f}%)")
print()

max_sector_pts = 1.0 * NEW_WEIGHTS["sector"] * 100
print(f"Structural max sector contribution to score (subscore=1.0): {max_sector_pts:.2f} points")
print(f"Structural min sector contribution to score (subscore=0.0): 0.00 points")
print()

print("=== New ceiling-hitters (score_new >= 99.999), if any ===")
ceiling_new = [r for r in rows if r["score_new"] >= 99.999]
if not ceiling_new:
    print("  none")
else:
    for r in ceiling_new:
        if r["has_earnings"]:
            core_only = (NEW_WEIGHTS["who"]*r["f1"] + NEW_WEIGHTS["buy_size"]*r["f2"] +
                         NEW_WEIGHTS["company_size"]*r["f3"] + NEW_WEIGHTS["earnings_timing"]*r["f4"])
        else:
            core_only = (NEW_W4_RENORM["who"]*r["f1"] + NEW_W4_RENORM["buy_size"]*r["f2"] +
                         NEW_W4_RENORM["company_size"]*r["f3"])
        print(f"  {r['ticker']:6s} {r['director']:30s} core-only-sum={core_only:.4f} has_earnings={r['has_earnings']}")
print()

# ---------------------------------------------------------------------------
# 8. Top 30 by OLD rank: old vs new
# ---------------------------------------------------------------------------
print("=== Top 30 by OLD rank: old score/rank vs new score/rank, sector subscore/points ===")
header = f"{'old#':>4} {'ticker':6} {'director':28} {'old_sc':>7} {'new_sc':>7} {'new#':>5} {'sector':22} {'net30d':>6} {'subscore':>8} {'pts(x20)':>8}"
print(header)
for r in sorted(rows, key=lambda r: r["rank_old"])[:30]:
    print(f"{r['rank_old']:>4} {r['ticker']:6} {r['director'][:28]:28} {r['score_old']:>7.2f} {r['score_new']:>7.2f} {r['rank_new']:>5} {str(r['sector'])[:22]:22} {r['sector_net30d']:>6} {r['sector_subscore']:>8.4f} {r['sector_points']:>8.2f}")
print()

# ---------------------------------------------------------------------------
# 9. New top 15 by NEW rank
# ---------------------------------------------------------------------------
print("=== New top 15 by NEW rank ===")
print(f"{'new#':>4} {'ticker':6} {'director':28} {'old#':>4} {'old_sc':>7} {'new_sc':>7} {'sector':22} {'pts(x20)':>8}")
for r in sorted_new[:15]:
    print(f"{r['rank_new']:>4} {r['ticker']:6} {r['director'][:28]:28} {r['rank_old']:>4} {r['score_old']:>7.2f} {r['score_new']:>7.2f} {str(r['sector'])[:22]:22} {r['sector_points']:>8.2f}")
print()

# ---------------------------------------------------------------------------
# 10. Spearman rank correlation (stdlib) vs today's live ordering
# ---------------------------------------------------------------------------
def spearman(rank_a, rank_b):
    n = len(rank_a)
    d2sum = sum((a - b) ** 2 for a, b in zip(rank_a, rank_b))
    return 1 - (6 * d2sum) / (n * (n**2 - 1))

ranks_old = [r["rank_old"] for r in rows]
ranks_new = [r["rank_new"] for r in rows]
rho = spearman(ranks_old, ranks_new)
print(f"=== Spearman rank correlation (new 5-factor vs current live ordering): rho = {rho:.4f} ===")
print()

PART2_ADJ_PTS = {
    "Financials": 4.45,
    "Industrials": 4.47,
    "Consumer Discretionary": 4.78,
    "Energy": 2.00,
    "Consumer Staples": 3.92,
    "Technology": -0.35,
    "Communication Services": 1.44,
}
proxy_rows = [r for r in rows if r["sector"] in PART2_ADJ_PTS]
if proxy_rows:
    for r in proxy_rows:
        if r["has_earnings"]:
            ws_old4 = (OLD_WEIGHTS["who"]*r["f1"] + OLD_WEIGHTS["buy_size"]*r["f2"] +
                       OLD_WEIGHTS["company_size"]*r["f3"] + OLD_WEIGHTS["earnings_timing"]*r["f4"])
        else:
            ws_old4 = (OLD_W3_RENORM["who"]*r["f1"] + OLD_W3_RENORM["buy_size"]*r["f2"] +
                       OLD_W3_RENORM["company_size"]*r["f3"])
        r["score_part2_proxy"] = max(0.0, min(100.0, 100*clamp01(ws_old4) + PART2_ADJ_PTS[r["sector"]]))
    print(f"(Proxy comparison to Part 2 additive design covers {len(proxy_rows)}/147 rows with mappable sectors from Part 2's printed table -- partial coverage, informal check only)")
    sorted_p2 = sorted(proxy_rows, key=lambda r: -r["score_part2_proxy"])
    for i, r in enumerate(sorted_p2, start=1):
        r["rank_part2_proxy"] = i
    # informal rho within the covered subset: new-design rank position (within subset) vs part2-proxy rank
    sorted_new_sub = sorted(proxy_rows, key=lambda r: -r["score_new"])
    for i, r in enumerate(sorted_new_sub, start=1):
        r["rank_new_sub"] = i
    rA = [r["rank_new_sub"] for r in proxy_rows]
    rB = [r["rank_part2_proxy"] for r in proxy_rows]
    rho_proxy = spearman(rA, rB)
    print(f"Informal Spearman (Part 3 5-factor vs Part 2 additive-nudge proxy, within {len(proxy_rows)}-row covered subset): rho = {rho_proxy:.4f}")
print()

# ---------------------------------------------------------------------------
# 11. Concrete sector examples: hot / neutral / cold
# ---------------------------------------------------------------------------
print("=== Concrete sector examples (hot / neutral / cold) ===")
examples = ["Financials", "Health Care", "Communication Services"]  # hot(24), mild(4), cold(-3)
for sec in examples:
    net = SECTOR_NET_30D[sec]
    sub = sigmoid_subscore(net, K_CHOSEN)
    pts = sub * 20
    print(f"  {sec:24s} net_30d={net:+3d}  subscore={sub:.4f}  points={pts:.2f} / 20")
print()

# ---------------------------------------------------------------------------
# 12. 20% weight max swing vs Part 2's +/-6pt cap comparison (arithmetic check)
# ---------------------------------------------------------------------------
print("=== 20% weight swing vs Part 2 cap ===")
print(f"  Part 3 sector max swing (subscore 0->1): {1.0*0.20*100:.1f} points (full range, 0.0 to {1.0*0.20*100:.1f})")
print(f"  Part 2 additive nudge cap: +/-6.0 points")
print(f"  Ratio: Part 3 max upside is {(1.0*0.20*100)/6.0:.2f}x Part 2's nudge cap")
print(f"  (Part 2 range -6 to +6 = 12pt span; Part 3 range 0 to +20 = 20pt span -> {20/12:.2f}x wider span)")
print(f"  New weight table for comparison: earnings_timing(new)={NEW_WEIGHTS['earnings_timing']*100:.1f}% company_size(new)={NEW_WEIGHTS['company_size']*100:.1f}% sector={NEW_WEIGHTS['sector']*100:.1f}%")
