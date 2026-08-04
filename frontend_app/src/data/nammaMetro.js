// ── Namma Metro — Bengaluru's current operational network ─────────
// All 3 currently-running lines (Green, Purple, Yellow), 85 stations,
// as of the 2026 network — matches the BMRCL reference map supplied
// for this feature (Purple: Whitefield–Challaghatta, Green:
// Madavara–Silk Institute, Yellow: RV Road–Bommasandra).
//
// Topology + coordinates: adapted from a CC0 public dataset
// (github.com/Vinayak-Chinchakhandi/Bengaluru-Metro-Network-Dataset),
// cross-checked station-by-station against the reference map — same
// 85 stations, same 2 interchanges (Majestic, RV Road).
//
// Colours: NOT from that dataset (its line_color column was a set of
// generic placeholder hues). Sampled directly, pixel-for-pixel, from
// the actual line strokes on the reference map instead, so the map
// matches BMRCL's real signage colours rather than an approximation.
export const METRO_LINE_COLORS = {
    'Green Line':  '#0E9546',
    'Purple Line': '#672F92',
    'Yellow Line': '#FFDD15',
};

// [station_code, station_name, line, sequence, is_interchange, lat, lon]
const RAW = [
  ['WHTM', 'Whitefield (Kadugodi)', 'Purple Line', 1, 0, 12.995699, 77.75773],
  ['UWVL', 'Hopefarm Channasandra', 'Purple Line', 2, 0, 12.987288, 77.753629],
  ['KDGD', 'Kadugodi Tree Park', 'Purple Line', 3, 0, 12.985649, 77.746494],
  ['ITPL', 'Pattandur Agrahara', 'Purple Line', 4, 0, 12.987593, 77.73774],
  ['SSHP', 'Sri Sathya Sai Hospital', 'Purple Line', 5, 0, 12.981164, 77.727343],
  ['VDHP', 'Nallurhalli', 'Purple Line', 6, 0, 12.97659, 77.724661],
  ['KDNH', 'Kundalahalli', 'Purple Line', 7, 0, 12.977654, 77.71558],
  ['VWIA', 'Seetharampalya', 'Purple Line', 8, 0, 12.981166, 77.708692],
  ['DKIA', 'Hoodi', 'Purple Line', 9, 0, 12.988861, 77.711246],
  ['GDCP', 'Garudacharapalya', 'Purple Line', 10, 0, 12.993565, 77.703542],
  ['MDVP', 'Singayyanapalya', 'Purple Line', 11, 0, 12.996618, 77.692363],
  ['KRAM', 'Krishnarajapura', 'Purple Line', 12, 0, 13.000109, 77.677621],
  ['BENN', 'Benniganahalli', 'Purple Line', 13, 0, 12.996388, 77.66818],
  ['BYPL', 'Baiyappanahalli', 'Purple Line', 14, 0, 12.990554, 77.652859],
  ['SVRD', 'Swami Vivekananda Road', 'Purple Line', 15, 0, 12.986017, 77.644813],
  ['IDN', 'Indiranagar', 'Purple Line', 16, 0, 12.978176, 77.638332],
  ['HLRU', 'Halasuru', 'Purple Line', 17, 0, 12.976462, 77.625994],
  ['TTY', 'Trinity', 'Purple Line', 18, 0, 12.972944, 77.616713],
  ['MAGR', 'Mahatma Gandhi Road', 'Purple Line', 19, 0, 12.975662, 77.606757],
  ['CBPK', 'Cubbon Park', 'Purple Line', 20, 0, 12.980973, 77.597315],
  ['VDSA', 'Dr B R Ambedkar Station Vidhana Soudha', 'Purple Line', 21, 0, 12.979865, 77.592723],
  ['VSWA', 'Sir M Visvesvaraya Central College', 'Purple Line', 22, 0, 12.974168, 77.584287],
  ['KGWA', 'Nadaprabhu Kempegowda Station Majestic', 'Purple Line', 23, 1, 12.97559, 77.573129],
  ['SRCS', 'Krantivira Sangolli Rayanna Railway Station', 'Purple Line', 24, 0, 12.976008, 77.565834],
  ['MIRD', 'Magadi Road', 'Purple Line', 25, 0, 12.975506, 77.555448],
  ['HSLI', 'Sri Balagangadharanatha Swamiji Hosahalli', 'Purple Line', 26, 0, 12.974126, 77.545192],
  ['VJN', 'Vijayanagar', 'Purple Line', 27, 0, 12.970906, 77.537209],
  ['AGPP', 'Attiguppe', 'Purple Line', 28, 0, 12.961915, 77.533948],
  ['DJNR', 'Deepanjali Nagar', 'Purple Line', 29, 0, 12.952086, 77.536823],
  ['MYRD', 'Mysuru Road', 'Purple Line', 30, 0, 12.946858, 77.529828],
  ['NYHM', 'Pantharapalya Nayandahalli', 'Purple Line', 31, 0, 12.941798, 77.523476],
  ['RRRN', 'Rajarajeshwari Nagar', 'Purple Line', 32, 0, 12.936695, 77.519185],
  ['BGUC', 'Jnanabharathi', 'Purple Line', 33, 0, 12.935315, 77.510945],
  ['PATG', 'Pattanagere', 'Purple Line', 34, 0, 12.92423, 77.498242],
  ['MLSD', 'Kengeri Bus Terminal', 'Purple Line', 35, 0, 12.914693, 77.487642],
  ['KGIT', 'Kengeri', 'Purple Line', 36, 0, 12.908001, 77.476355],
  ['CHLG', 'Challaghatta', 'Purple Line', 37, 0, 12.89743, 77.461109],
  ['MDVA', 'Madavara', 'Green Line', 1, 0, 13.057306, 77.472845],
  ['CKBL', 'Chikkabidarakallu', 'Green Line', 2, 0, 13.052349, 77.487821],
  ['MJNN', 'Manjunath Nagar', 'Green Line', 3, 0, 13.05031, 77.494353],
  ['NGSA', 'Nagasandra', 'Green Line', 4, 0, 13.048236, 77.500163],
  ['DSH', 'Dasarahalli', 'Green Line', 5, 0, 13.04363, 77.512324],
  ['JLHL', 'Jalahalli', 'Green Line', 6, 0, 13.039833, 77.519939],
  ['PYID', 'Peenya Industry', 'Green Line', 7, 0, 13.036458, 77.525279],
  ['PEYA', 'Peenya', 'Green Line', 8, 0, 13.033118, 77.533327],
  ['YPI', 'Goraguntepalya', 'Green Line', 9, 0, 13.028302, 77.540833],
  ['YPM', 'Yeshwanthpur', 'Green Line', 10, 0, 13.023274, 77.549783],
  ['SSFY', 'Sandal Soap Factory', 'Green Line', 11, 0, 13.01473, 77.553933],
  ['MHLI', 'Mahalakshmi', 'Green Line', 12, 0, 13.008261, 77.549025],
  ['RJNR', 'Rajajinagar', 'Green Line', 13, 0, 13.000384, 77.549783],
  ['KVPR', 'Mahakavi Kuvempu Road', 'Green Line', 14, 0, 12.998415, 77.556784],
  ['SPRU', 'Srirampura', 'Green Line', 15, 0, 12.996622, 77.563424],
  ['SPGD', 'Mantri Square Sampige Road', 'Green Line', 16, 0, 12.990328, 77.570641],
  ['KGWA', 'Nadaprabhu Kempegowda Station Majestic', 'Green Line', 17, 1, 12.975664, 77.572662],
  ['CKPE', 'Chickpete', 'Green Line', 18, 0, 12.967492, 77.57463],
  ['KRMT', 'Krishna Rajendra Market', 'Green Line', 19, 0, 12.959932, 77.574476],
  ['NLC', 'National College', 'Green Line', 20, 0, 12.950632, 77.573609],
  ['LBGH', 'Lalbagh', 'Green Line', 21, 0, 12.946255, 77.579784],
  ['SECE', 'South End Circle', 'Green Line', 22, 0, 12.938446, 77.579988],
  ['JYN', 'Jayanagar', 'Green Line', 23, 0, 12.929841, 77.579886],
  ['RVR', 'Rashtreeya Vidyalaya Road', 'Green Line', 24, 1, 12.921683, 77.580346],
  ['BSNK', 'Banashankari', 'Green Line', 25, 0, 12.915614, 77.573507],
  ['JPN', 'Jayaprakash Nagar', 'Green Line', 26, 0, 12.907755, 77.572945],
  ['PUTH', 'Yelachenahalli', 'Green Line', 27, 0, 12.896263, 77.569985],
  ['APRC', 'Konanakunte Cross', 'Green Line', 28, 0, 12.888801, 77.562534],
  ['KLPK', 'Doddakallasandra', 'Green Line', 29, 0, 12.884672, 77.552838],
  ['VJRH', 'Vajarahalli', 'Green Line', 30, 0, 12.877209, 77.544621],
  ['TGTP', 'Thalaghattapura', 'Green Line', 31, 0, 12.871288, 77.538292],
  ['APTS', 'Silk Institute', 'Green Line', 32, 0, 12.861885, 77.529923],
  ['RVR', 'Rashtreeya Vidyalaya Road', 'Yellow Line', 1, 1, 12.92158, 77.580304],
  ['RAGI', 'Ragigudda', 'Yellow Line', 2, 0, 12.916937, 77.588115],
  ['JDEV', 'Jayadeva Hospital', 'Yellow Line', 3, 0, 12.916581, 77.599809],
  ['BTML', 'BTM Layout', 'Yellow Line', 4, 0, 12.916393, 77.608113],
  ['CSBR', 'Central Silk Board', 'Yellow Line', 5, 0, 12.91631, 77.620409],
  ['BOMN', 'Bommanahalli', 'Yellow Line', 6, 0, 12.910788, 77.626395],
  ['HONG', 'Hongasandra', 'Yellow Line', 7, 0, 12.901564, 77.632017],
  ['KUDG', 'Kudlu Gate', 'Yellow Line', 8, 0, 12.88996, 77.639392],
  ['SING', 'Singasandra', 'Yellow Line', 9, 0, 12.880606, 77.644597],
  ['HSRD', 'Hosa Road', 'Yellow Line', 10, 0, 12.870955, 77.652406],
  ['BTAG', 'Beratena Agrahara', 'Yellow Line', 11, 0, 12.856277, 77.663327],
  ['ELCT', 'Electronic City', 'Yellow Line', 12, 0, 12.85654, 77.663284],
  ['INFO', 'Infosys Foundation Konappana Agrahara', 'Yellow Line', 13, 0, 12.846265, 77.671118],
  ['HUSK', 'Huskur Road', 'Yellow Line', 14, 0, 12.838802, 77.677447],
  ['BIOC', 'Biocon Hebbagodi', 'Yellow Line', 15, 0, 12.828711, 77.68124],
  ['DELT', 'Delta Electronics Bommasandra', 'Yellow Line', 16, 0, 12.819555, 77.688743],
];

// One polyline per line, stations already in physical/travel order.
export function buildMetroLines() {
    const byLine = new Map();
    for (const [code, name, line, seq, interchange, lat, lon] of RAW) {
        if (!byLine.has(line)) byLine.set(line, []);
        byLine.get(line).push({ code, name, seq, lat, lon });
    }
    return [...byLine.entries()].map(([line, stations]) => ({
        line,
        color: METRO_LINE_COLORS[line] || '#999',
        stations: stations.sort((a, b) => a.seq - b.seq),
    }));
}

// De-duplicated station list — interchange stations (Majestic, RV Road)
// appear once per line in RAW; collapse each into a single marker that
// lists every line it serves, instead of stacking two overlapping pins.
export function buildMetroStations() {
    const map = new Map();
    for (const [code, name, line, , interchange, lat, lon] of RAW) {
        if (!map.has(code)) {
            map.set(code, { code, name, lat, lon, lines: [], interchange: false });
        }
        const s = map.get(code);
        s.lines.push(line);
        if (interchange) s.interchange = true;
    }
    return [...map.values()];
}
