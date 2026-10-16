/* Labour guidance adapted from the company supplied v4.4 calculator. No rates, network calls or automatic quote approval. */
(function(){
"use strict";
const TIME_INCREMENT=0.25;
  var PROFILES = {
    furniture: {
      label: "Furniture delivery / micro-move",
      boxes: 10,
      baseCrew: 2,
      maxCrew: 3,
      trucks: 1,
      low: 1.50,
      high: 2.50,
      factor: 0.65
    },

    studio: {
      label: "Studio apartment",
      boxes: 20,
      baseCrew: 2,
      maxCrew: 3,
      trucks: 1,
      low: 2.75,
      high: 3.50,
      factor: 0.80
    },

    oneLight: {
      label: "Light 1 bedroom apartment",
      boxes: 20,
      baseCrew: 2,
      maxCrew: 3,
      trucks: 1,
      low: 3.25,
      high: 4.25,
      factor: 0.95
    },

    one: {
      label: "1 bedroom apartment",
      boxes: 20,
      baseCrew: 2,
      maxCrew: 3,
      trucks: 1,
      low: 3.75,
      high: 5.00,
      factor: 1.00
    },

    twoLight: {
      label: "Light 2 bedroom apartment",
      boxes: 20,
      baseCrew: 2,
      maxCrew: 4,
      trucks: 1,
      low: 4.50,
      high: 6.00,
      factor: 1.18
    },

    twoApartment: {
      label: "2 bedroom apartment",
      boxes: 30,
      baseCrew: 3,
      maxCrew: 4,
      trucks: 1,
      low: 4.25,
      high: 5.75,
      factor: 1.25
    },

    twoHome: {
      label: "2 bedroom home",
      boxes: 40,
      baseCrew: 3,
      maxCrew: 4,
      trucks: 1,
      low: 5.50,
      high: 7.50,
      factor: 1.45
    },

    threeApartment: {
      label: "3 bedroom apartment",
      boxes: 50,
      baseCrew: 4,
      maxCrew: 5,
      trucks: 2,
      low: 5.00,
      high: 6.50,
      factor: 1.55
    },

    threeHome: {
      label: "3 bedroom home",
      boxes: 60,
      baseCrew: 4,
      maxCrew: 5,
      trucks: 2,
      low: 6.00,
      high: 8.00,
      factor: 1.75
    },

    fourHome: {
      label: "4 bedroom home",
      boxes: 70,
      baseCrew: 5,
      maxCrew: 6,
      trucks: 2,
      low: 6.00,
      high: 8.00,
      factor: 1.95,
      review: true
    },

    larger: {
      label: "More than 4 bedrooms / full house",
      boxes: 90,
      baseCrew: 6,
      maxCrew: 6,
      trucks: 3,
      low: 7.00,
      high: 10.00,
      factor: 2.20,
      review: true
    },

    storage: {
      label: "Storage facility",
      boxes: 20,
      baseCrew: 2,
      maxCrew: 3,
      trucks: 1,
      low: 2.50,
      high: 4.00,
      factor: 1.00
    }
  };

  function clean(value) {
    return String(value || "").replace(/\s+/g, " ").trim();
  }

  function lower(value) {
    return clean(value).toLowerCase();
  }

  function unique(list) {
    var seen = {};

    return (list || []).filter(function (item) {
      item = clean(item);

      if (!item || seen[item]) {
        return false;
      }

      seen[item] = true;
      return true;
    });
  }

  function moneyCents(value) {
    return "$" + Number(value || 0).toFixed(2);
  }

  function roundNearest(value) {
    return Math.round(Number(value || 0) / TIME_INCREMENT) * TIME_INCREMENT;
  }

  function roundUp(value) {
    return Math.ceil(
      (Number(value || 0) - 0.000001) / TIME_INCREMENT
    ) * TIME_INCREMENT;
  }

  function accessDetails(rawValue) {
    var text = lower(rawValue);

    if (/not sure|unsure/.test(text)) {
      return {
        points: 2,
        flights: 0,
        elevator: false,
        direct: false,
        review: true,
        warning:
          "Access was marked not sure."
      };
    }

    if (
      /freight/.test(text) &&
      /elevator/.test(text)
    ) {
      return {
        points: 1.5,
        flights: 0,
        elevator: true,
        direct: false,
        review: false,
        warning:
          "Freight elevator details should be confirmed."
      };
    }

    if (/elevator|lift/.test(text)) {
      return {
        points: 2,
        flights: 0,
        elevator: true,
        direct: false,
        review: false,
        warning:
          "Elevator reservation, size, and walking distance should be confirmed."
      };
    }

    if (
      /4\+|4 flight|four flight/.test(text)
    ) {
      return {
        points: 4,
        flights: 4,
        elevator: false,
        direct: false,
        review: true,
        warning:
          "Four or more flights require review."
      };
    }

    if (
      /3 flight|three flight/.test(text)
    ) {
      return {
        points: 3,
        flights: 3,
        elevator: false,
        direct: false,
        review: false,
        warning: ""
      };
    }

    if (
      /2 flight|two flight/.test(text)
    ) {
      return {
        points: 2,
        flights: 2,
        elevator: false,
        direct: false,
        review: false,
        warning: ""
      };
    }

    if (
      /1 flight|one flight/.test(text)
    ) {
      return {
        points: 1,
        flights: 1,
        elevator: false,
        direct: false,
        review: false,
        warning: ""
      };
    }

    if (
      /few/.test(text) &&
      /step/.test(text)
    ) {
      return {
        points: 0.5,
        flights: 0,
        elevator: false,
        direct: false,
        review: false,
        warning: ""
      };
    }

    return {
      points: 0,
      flights: 0,
      elevator: false,
      direct: true,
      review: false,
      warning: ""
    };
  }

  function walkDetails(rawValue) {
    var text = lower(rawValue);

    if (/not sure|unsure/.test(text)) {
      return {
        low: 0.25,
        high: 0.50,
        score: 2,
        review: true,
        warning:
          "Walking distance was marked not sure."
      };
    }

    if (
      /100\+|more than 100|over 100/.test(text)
    ) {
      return {
        low: 1.00,
        high: 1.50,
        score: 6,
        review: true,
        warning:
          "More than 100 feet of walking requires review."
      };
    }

    if (
      /50 to 100|50-100|50\+|^50 feet$|^50 ft$/.test(
        text
      )
    ) {
      return {
        low: 0.60,
        high: 1.00,
        score: 5,
        review: false,
        warning:
          "A 50–100 ft carry may add time."
      };
    }

    if (
      /30 to 50|30-50|30\+/.test(text)
    ) {
      return {
        low: 0.30,
        high: 0.50,
        score: 4,
        review: false,
        warning:
          "A 30–50 ft carry may add time."
      };
    }

    if (/20 to 30|20-30/.test(text)) {
      return {
        low: 0.20,
        high: 0.30,
        score: 3,
        review: false,
        warning: ""
      };
    }

    if (/10 to 20|10-20/.test(text)) {
      return {
        low: 0.10,
        high: 0.20,
        score: 2,
        review: false,
        warning: ""
      };
    }

    return {
      low: 0,
      high: 0,
      score: 0,
      review: false,
      warning: ""
    };
  }

  function boxDetails(rawValue) {
    var text = lower(rawValue).replace(/–/g, "-");

    if (/not sure|unsure/.test(text)) {
      return {
        low: 0,
        high: 0,
        average: 0,
        score: 4,
        unsure: true
      };
    }

    if (/no boxes|none/.test(text)) {
      return {
        low: 0,
        high: 0,
        average: 0,
        score: 0,
        unsure: false
      };
    }

    if (/120\s*\+/.test(text)) {
      return {
        low: 120,
        high: 140,
        average: 130,
        score: 10,
        unsure: false
      };
    }

    var match = text.match(
      /(\d+)\s*(?:to|-)\s*(\d+)/
    );

    if (match) {
      var lowValue = Number(match[1]);
      var highValue = Number(match[2]);

      return {
        low: lowValue,
        high: highValue,
        average:
          (lowValue + highValue) / 2,

        score:
          highValue <= 10
            ? 1
            : highValue <= 20
            ? 2
            : highValue <= 30
            ? 3
            : highValue <= 40
            ? 4
            : highValue <= 50
            ? 5
            : highValue <= 60
            ? 6
            : highValue <= 80
            ? 7
            : highValue <= 100
            ? 8
            : highValue <= 120
            ? 9
            : 10,

        unsure: false
      };
    }

    var exact = text.match(/\b(\d+)\b/);

    if (exact) {
      var number = Number(exact[1]);

      return {
        low: number,
        high: number,
        average: number,

        score:
          number <= 10
            ? 1
            : number <= 20
            ? 2
            : number <= 30
            ? 3
            : number <= 40
            ? 4
            : number <= 50
            ? 5
            : number <= 60
            ? 6
            : number <= 80
            ? 7
            : number <= 100
            ? 8
            : number <= 120
            ? 9
            : 10,

        unsure: false
      };
    }

    return {
      low: 0,
      high: 0,
      average: 0,
      score: 0,
      unsure: false
    };
  }

  function notesAnalysis(lead, intake) {
    var text = lower([lead.inventory, intake.service_details, (intake.additional_services || []).join(", ")].join(" "));

    var appliances = unique(
      text.match(
        /\b(fridge|refrigerator|stove|washer|dryer|freezer|dishwasher|laveuse|s[eé]cheuse|r[eé]frig[eé]rateur|cuisini[eè]re|cong[eé]lateur)\b/g
      ) || []
    );

    var areas = unique(
      text.match(
        /\b(garage|basement|sous-sol|shed|cabanon|storage locker|locker|balcony|balcon|patio|attic|grenier|workshop|atelier)\b/g
      ) || []
    );

    var warnings = [];
    var low = 0;
    var high = 0;

    if (appliances.length) {
      low += Math.min(
        0.30,
        appliances.length * 0.10
      );

      high += Math.min(
        0.60,
        appliances.length * 0.20
      );

      warnings.push(
        "Major appliances were mentioned and should be disconnected and ready."
      );
    }

    if (areas.length) {
      low += Math.min(
        0.50,
        areas.length * 0.20
      );

      high += Math.min(
        1.00,
        areas.length * 0.40
      );

      warnings.push(
        "Extra areas such as a basement, garage, shed, balcony, or storage locker may increase time and volume."
      );
    }

    if (
      /disassembl|dismantl|reassembl|pax|wardrobe|armoire|bed frame|cadre de lit/.test(
        text
      )
    ) {
      low += 0.20;
      high += 0.50;

      warnings.push(
        "Disassembly or reassembly may add time."
      );
    }

    if (
      /loose items|unpacked|not packed|items not boxed|non emball/.test(
        text
      )
    ) {
      low += 0.25;
      high += 0.75;

      warnings.push(
        "Loose or unpacked items may reduce loading efficiency."
      );
    }

    return {
      text: text,
      appliances: appliances.length,
      areas: areas.length,
      low: low,
      high: high,

      tight:
        /tight stair|narrow stair|escalier [eé]troit|narrow hallway|tight hallway/.test(
          text
        ),

      deadline:
        /deadline|must be out|elevator reservation|reserved elevator|time-sensitive|strict time/.test(
          text
        ),

      fullVolume:
        /full truck|filled a truck|high volume|entire house|whole house|very full|large inventory/.test(
          text
        ),

      afternoon:
        /afternoon|pm service|apr[eè]s-midi/.test(
          text
        ),

      warnings: warnings
    };
  }

  function homeCategory(rawValue) {
    var text = lower(rawValue);

    if (
      /furniture|few pieces|delivery|micro/.test(
        text
      )
    ) {
      return "furniture";
    }

    if (/storage/.test(text)) {
      return "storage";
    }

    if (
      /larger|more than 4|5 bedroom|6 bedroom|full house/.test(
        text
      )
    ) {
      return "larger";
    }

    if (/studio/.test(text)) {
      return "studio";
    }

    if (/1 bedroom|one bedroom/.test(text)) {
      return "one";
    }

    if (
      /(2 bedroom|two bedroom)/.test(text) &&
      /home|house/.test(text)
    ) {
      return "twoHome";
    }

    if (/2 bedroom|two bedroom/.test(text)) {
      return "twoApartment";
    }

    if (
      /(3 bedroom|three bedroom)/.test(text) &&
      /home|house/.test(text)
    ) {
      return "threeHome";
    }

    if (
      /3 bedroom|three bedroom|small 3/.test(
        text
      )
    ) {
      return "threeApartment";
    }

    if (/4 bedroom|four bedroom/.test(text)) {
      return "fourHome";
    }

    return "";
  }

  function selectProfile(
    inputs,
    heavyItems,
    notes
  ) {
    var category = homeCategory(inputs.home);
    var pickup = accessDetails(
      inputs.pickupAccess
    );

    var dropoff = accessDetails(
      inputs.dropoffAccess
    );

    var pickupWalk = walkDetails(
      inputs.walkPickup
    );

    var dropoffWalk = walkDetails(
      inputs.walkDropoff
    );

    var boxes = boxDetails(inputs.boxes);

    var flights =
      pickup.flights + dropoff.flights;

    var bothDirect =
      pickup.direct && dropoff.direct;

    var bothElevators =
      pickup.elevator && dropoff.elevator;

    var longWalk =
      pickupWalk.score >= 4 ||
      dropoffWalk.score >= 4;

    var hasHeavy =
      heavyItems.length > 0;

    var key = category;

    if (category === "one") {
      key =
        boxes.high <= 20 &&
        !notes.appliances &&
        !hasHeavy &&
        !longWalk &&
        !notes.tight
          ? "oneLight"
          : "one";
    }

    if (category === "twoApartment") {
      key =
        bothDirect &&
        boxes.high <= 20 &&
        !notes.appliances &&
        !hasHeavy &&
        !longWalk
          ? "twoLight"
          : "twoApartment";
    }

    var source = PROFILES[key];

    if (!source) {
      return null;
    }

    var profile = Object.assign(
      {
        key: key,
        category: category
      },
      source
    );

    profile.crew = profile.baseCrew;

    if (category === "one") {
      var failedThreeFlightException =
        flights === 3 &&
        (
          boxes.high > 20 ||
          notes.appliances ||
          hasHeavy ||
          longWalk ||
          notes.tight
        );

      if (
        flights >= 4 ||
        failedThreeFlightException ||
        boxes.high > 20 ||
        notes.appliances ||
        hasHeavy
      ) {
        profile.crew = 3;
      }
    }

    if (category === "twoApartment") {
      if (bothElevators) {
        profile.crew = 3;
      }

      if (
        flights >= 4 &&
        !bothElevators
      ) {
        profile.crew = 4;
      }
    }

    if (
      category === "twoHome" &&
      flights >= 4 &&
      !bothElevators
    ) {
      profile.crew = 4;
    }

    if (
      category === "studio" &&
      (
        flights >= 5 ||
        notes.appliances ||
        hasHeavy
      )
    ) {
      profile.crew = 3;
    }

    profile.crew = Math.min(
      profile.crew,
      profile.maxCrew
    );

    profile.review = Boolean(
      profile.review ||
      pickup.review ||
      dropoff.review ||
      pickupWalk.review ||
      dropoffWalk.review
    );

    return profile;
  }

  function crewScale(movers) {
    return movers <= 2
      ? 1
      : movers === 3
      ? 0.90
      : movers === 4
      ? 0.82
      : movers === 5
      ? 0.78
      : 0.75;
  }

  function speedFactor(movers) {
    return movers <= 2
      ? 1
      : movers === 3
      ? 0.78
      : movers === 4
      ? 0.65
      : movers === 5
      ? 0.58
      : 0.52;
  }

  function accessTime(
    profile,
    inputs,
    movers
  ) {
    var pickup = accessDetails(
      inputs.pickupAccess
    );

    var dropoff = accessDetails(
      inputs.dropoffAccess
    );

    var points =
      pickup.points + dropoff.points;

    var low =
      points <= 0
        ? 0
        : points <= 2
        ? 0.25
        : points <= 4
        ? 0.50
        : points <= 6
        ? 1.00
        : 1.50;

    var high =
      points <= 0
        ? 0
        : points <= 2
        ? 0.50
        : points <= 4
        ? 1.00
        : points <= 6
        ? 1.50
        : 2.25;

    var size = Math.max(
      0.80,
      Math.min(
        1.35,
        0.85 +
          (profile.factor - 1) * 0.25
      )
    );

    return {
      low:
        low *
        size *
        crewScale(movers),

      high:
        high *
        size *
        crewScale(movers),

      review:
        pickup.review ||
        dropoff.review ||
        points >= 7,

      warnings: unique([
        pickup.warning,
        dropoff.warning
      ])
    };
  }

  function walkingTime(
    profile,
    inputs,
    movers
  ) {
    var pickup = walkDetails(
      inputs.walkPickup
    );

    var dropoff = walkDetails(
      inputs.walkDropoff
    );

    var size = Math.max(
      0.85,
      Math.min(
        1.30,
        0.90 +
          (profile.factor - 1) * 0.22
      )
    );

    return {
      low:
        (pickup.low + dropoff.low) *
        size *
        crewScale(movers),

      high:
        (pickup.high + dropoff.high) *
        size *
        crewScale(movers),

      review:
        pickup.review ||
        dropoff.review,

      warnings: unique([
        pickup.warning,
        dropoff.warning
      ])
    };
  }

  function extraBoxTime(
    profile,
    inputs,
    movers
  ) {
    var boxes = boxDetails(inputs.boxes);

    if (boxes.unsure) {
      return {
        low: 0.25,
        high: 0.75,
        review: true,
        warning:
          "Box count was marked not sure."
      };
    }

    if (boxes.high <= profile.boxes) {
      return {
        low: 0,
        high: 0,
        review: false,
        warning: ""
      };
    }

    var groups =
      Math.max(
        0,
        boxes.average - profile.boxes
      ) / 10;

    var lowRate =
      movers <= 2
        ? 0.25
        : movers === 3
        ? 0.10
        : movers === 4
        ? 0.05
        : 0;

    var highRate =
      movers <= 2
        ? 0.50
        : movers === 3
        ? 0.20
        : movers === 4
        ? 0.15
        : 0.10;

    var difficulty =
      1 +
      (
        accessDetails(
          inputs.pickupAccess
        ).points +
        accessDetails(
          inputs.dropoffAccess
        ).points
      ) *
        0.08 +
      (
        walkDetails(
          inputs.walkPickup
        ).score +
        walkDetails(
          inputs.walkDropoff
        ).score
      ) *
        0.05;

    return {
      low:
        groups *
        lowRate *
        difficulty,

      high:
        groups *
        highRate *
        difficulty,

      review:
        boxes.high > 120,

      warning:
        "Box count is above the normal range for this move size."
    };
  }

  function specialtyRule(item) {
    var text = lower(item);

    if (/piano/.test(text)) {
      return {
        label: "Upright piano",
        fee: 337.50,
        hours: 0.50,
        note:
          "Piano type, weight, photos, and access must be reviewed."
      };
    }

    if (/safe/.test(text)) {
      return {
        label: "Safe",
        fee: 225.00,
        hours: 0.50,
        note:
          "Safe weight and access must be confirmed."
      };
    }

    if (
      /commercial fridge|large appliance/.test(
        text
      )
    ) {
      return {
        label:
          "Large appliance / commercial fridge",
        fee: 337.50,
        hours: 0.50,
        note:
          "The appliance must be disconnected and ready."
      };
    }

    if (/pool table/.test(text)) {
      return {
        label: "Pool table",
        fee: 900.00,
        hours: 1.00,
        note:
          "Pool-table handling must be reviewed."
      };
    }

    if (/oversized couch/.test(text)) {
      return {
        label: "Oversized couch",
        fee: 187.50,
        hours: 0.25,
        note:
          "Oversized-couch access must be confirmed."
      };
    }

    if (/large glass/.test(text)) {
      return {
        label: "Large glass item",
        fee: 187.50,
        hours: 0.35,
        note:
          "Extra protection may be required."
      };
    }

    if (/marble|granite/.test(text)) {
      return {
        label: "Marble / granite item",
        fee: 187.50,
        hours: 0.50,
        note:
          "Stone-item protection and access must be reviewed."
      };
    }

    if (/treadmill/.test(text)) {
      return {
        label: "Large treadmill",
        fee: 225.00,
        hours: 0.50,
        note:
          "Treadmill access and disassembly must be confirmed."
      };
    }

    if (/gym/.test(text)) {
      return {
        label: "Gym equipment",
        fee: 187.50,
        hours: 0.50,
        note:
          "Gym-equipment details must be reviewed."
      };
    }

    if (/other|not sure/.test(text)) {
      return {
        label: "Other / Not sure",
        fee: 187.50,
        hours: 0.50,
        note:
          "Photos and details should be reviewed."
      };
    }

    return {
      label: item,
      fee: 0,
      hours: 0,
      note: ""
    };
  }

  function specialtyEstimate(items) {
    var result = {
      fee: 0,
      hours: 0,
      notes: [],
      details: [],
      count: 0
    };

    (items || [])
      .filter(function (item) {
        return (
          lower(item).indexOf("none") === -1
        );
      })
      .forEach(function (item) {
        var rule = specialtyRule(item);

        result.count += 1;
        result.fee += rule.fee;
        result.hours += rule.hours;

        result.details.push(
          rule.label +
            ": " +
            moneyCents(rule.fee)
        );

        if (rule.note) {
          result.notes.push(
            rule.label + ": " + rule.note
          );
        }
      });

    return result;
  }

  function truckCount(
    profile,
    boxes,
    notes,
    heavyItems
  ) {
    var count = profile.trucks;

    if (
      profile.key === "twoHome" &&
      (
        boxes.high > 60 ||
        notes.fullVolume ||
        notes.areas >= 2
      )
    ) {
      count = 2;
    }

    if (
      (
        profile.key === "twoApartment" ||
        profile.key === "twoLight"
      ) &&
      (
        boxes.high > 80 ||
        notes.fullVolume
      )
    ) {
      count = 2;
    }

    if (
      [
        "studio",
        "one",
        "oneLight",
        "storage"
      ].indexOf(profile.key) > -1 &&
      boxes.high > 80
    ) {
      count = 2;
    }

    if (boxes.high > 100) {
      count = Math.max(count, 2);
    }

    if (profile.key === "larger") {
      count = 3;
    }

    if (
      heavyItems.some(function (item) {
        return /pool table|gym|commercial/i.test(
          item
        );
      }) &&
      profile.factor >= 1.25
    ) {
      count = Math.max(count, 2);
    }

    return count;
  }

  function calculateService(
    profile,
    movers,
    inputs,
    specialty,
    notes,
    route
  ) {
    var access = accessTime(
      profile,
      inputs,
      movers
    );

    var walking = walkingTime(
      profile,
      inputs,
      movers
    );

    var boxes = extraBoxTime(
      profile,
      inputs,
      movers
    );

    var baseRatio =
      speedFactor(movers) /
      speedFactor(profile.baseCrew);

    var labourLow =
      profile.low * baseRatio +
      access.low +
      walking.low +
      boxes.low +
      notes.low * crewScale(movers) +
      specialty.hours *
        crewScale(movers);

    var labourHigh =
      profile.high * baseRatio +
      access.high +
      walking.high +
      boxes.high +
      notes.high * crewScale(movers) +
      specialty.hours *
        crewScale(movers);

    return {
      labourLow: labourLow,

      labourHigh:
        Math.max(
          labourLow,
          labourHigh
        ),

      low:
        labourLow +
        route.driveLow,

      high:
        Math.max(
          labourLow +
            route.driveLow,
          labourHigh +
            route.driveHigh
        ),

      access: access,
      walking: walking,
      boxes: boxes
    };
  }

  function uncertaintyCount(inputs) {
    return [
      inputs.pickupAccess,
      inputs.dropoffAccess,
      inputs.walkPickup,
      inputs.walkDropoff,
      inputs.boxes
    ].filter(function (item) {
      return /not sure|unsure/i.test(item);
    }).length;
  }

  function suggest(lead) {
    let intake=lead.intake||{};
    try{if(lead.intake_json)intake=JSON.parse(lead.intake_json);}catch{}
    if(intake.local_area!=='Yes'||!['Moving','Form',''].includes(lead.lead_type||''))return {available:false,reason:'Choose Local Montreal = Yes for a local moving suggestion. Other services use their own quote template.'};
    const required=['home_size','boxes','pickup_access','dropoff_access','pickup_walk','dropoff_walk'];
    const missing=required.filter(k=>!String(lead[k]||'').trim());
    if(missing.length)return {available:false,reason:'Complete '+missing.map(k=>k.replaceAll('_',' ')).join(', ')+'. You can still build an estimate manually.'};
    const access=v=>/^\d+$/.test(v)?(Number(v)===0?'Ground floor / direct access':v+' flights of stairs'):v;
    const walk=v=>/^\d+$/.test(v)?(Number(v)<=10?'0-10 ft':Number(v)<=20?'10-20 ft':Number(v)<=30?'20-30 ft':Number(v)<=50?'30-50 ft':Number(v)<=100?'50-100 ft':'100+ ft'):v;
    const inputs={home:lead.home_size,boxes:lead.boxes,pickupAccess:access(lead.pickup_access),dropoffAccess:access(lead.dropoff_access),walkPickup:walk(lead.pickup_walk),walkDropoff:walk(lead.dropoff_walk)};
    const accessKnown=v=>/ground|direct|few.*step|[1-4].*flight|one flight|two flight|three flight|four flight|elevator|lift|not sure|unsure/i.test(v);
    const walkKnown=v=>/0-10|10[- ](?:to )?20|20[- ](?:to )?30|30[- ](?:to )?50|50[- ](?:to )?100|100\+|not sure|unsure/i.test(v);
    if(!accessKnown(inputs.pickupAccess)||!accessKnown(inputs.dropoffAccess)||!walkKnown(inputs.walkPickup)||!walkKnown(inputs.walkDropoff))return {available:false,reason:'Use the suggested access and walking ranges, or estimate custom access details manually.'};
    const heavy=unique([...(intake.heavy_items||[]),...String(lead.heavy_items||'').split(/[,\n]/)]).filter(x=>!/^none$/i.test(x));
    const notes=notesAnalysis(lead,intake),specialty=specialtyEstimate(heavy),boxes=boxDetails(inputs.boxes),profile=selectProfile(inputs,heavy,notes);
    if(!profile)return {available:false,reason:'Select a supported move size, or estimate this service manually.'};
    const known=v=>v!==''&&v!==null&&v!==undefined&&Number.isFinite(Number(v))&&Number(v)>=0&&Number(v)<=1440;
    const driveKnown=known(intake.between_stops_minutes),drive=driveKnown?Number(intake.between_stops_minutes)/60:0;
    const route={driveLow:drive,driveHigh:drive};
    let movers=profile.crew,service=calculateService(profile,movers,inputs,specialty,notes,route);
    for(const [crew,threshold] of [[2,6],[3,8],[4,8]]){
      if(movers===crew&&(crew===2?service.labourHigh>threshold:service.labourHigh>=threshold)&&profile.maxCrew>=crew+1){movers++;service=calculateService(profile,movers,inputs,specialty,notes,route);}
    }
    const trucks=Math.max(truckCount(profile,boxes,notes,heavy),movers>=6?3:movers>=4?2:1);
    const unsure=uncertaintyCount(inputs);service.high+=Math.min(3,unsure)*0.25;
    const small=profile.key==='furniture'&&boxes.high<=20&&!specialty.count&&intake.start_window==='afternoon';
    const minimum=small?1.5:3,low=Math.max(minimum,roundNearest(service.low)),high=Math.max(low,roundUp(service.high));
    const baseKnown=known(intake.pickup_base_minutes)&&known(intake.return_base_minutes);
    const transport=baseKnown?(Number(intake.pickup_base_minutes)>45&&Number(intake.return_base_minutes)>45?1.5:Number(intake.pickup_base_minutes)>45||Number(intake.return_base_minutes)>45?1.25:1):null;
    const warnings=unique([...notes.warnings,...specialty.notes,...service.access.warnings,...service.walking.warnings,service.boxes.warning,
      !driveKnown?'Driving time between stops is unknown. This range is labour only; enter driving minutes before applying it.':'',
      !baseKnown?'Base travel is unknown. Review the separate transportation allowance.':'',
      profile.review||unsure?'Uncertain or complex details need staff review.':'',
      (intake.additional_services||[]).some(x=>/packing|storage|junk/i.test(x))?'Quote packing, storage or junk removal separately when needed.':'',
      heavy.length?'Review any specialty charges separately. This suggestion adds handling time, not fees.':'']);
    return {available:true,can_apply:driveKnown,profile:profile.label,movers,trucks,hours_low:low,hours_high:high,minimum_hours:minimum,small_afternoon_job:small,start_window:intake.start_window==='afternoon'?'afternoon':'morning',transport_hours:transport,warnings,source:'Crown residential calculator v4.4 — labour guidance'};
  }
  const api={suggest};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;
  if(typeof window!=='undefined')window.CrownLocalEstimator=api;
})();
