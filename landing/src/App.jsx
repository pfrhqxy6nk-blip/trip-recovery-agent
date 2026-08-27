import { useEffect, useRef, useState } from "react";

const watchpoints = [
  ["Flight", "LH 247 · Warsaw → Munich", "Live"],
  ["Connection", "Munich Airport · 1h 35m", "Protected"],
  ["Stay", "Hotel Torbräu · late arrival", "Ready"],
  ["Weather", "Munich · route-aware alerts", "Watching"],
];

const flightScenes = [
  {
    eyebrow: "Trip Watch is following along",
    title: <>Your journey stays human.<br />The details do not have to.</>,
    copy: "It quietly watches the connections around the plan you already made.",
  },
  {
    eyebrow: "A change is becoming a plan",
    title: <>One delay.<br />Everything around it understood.</>,
    copy: "The agent follows the transfer, hotel and calendar impact before it reaches you.",
  },
  {
    eyebrow: "Only the decision returns to you",
    title: <>The safe work is done.<br />You keep the choice.</>,
    copy: "You see the cost, the reason and the next best option in Telegram.",
  },
];

const planningScenes = [
  {
    eyebrow: "05 · Plan from a sentence",
    title: <>Tell us where you want to go.<br />We’ll build the route.</>,
    copy: "Say “Paris for six nights, under €600.” Gemini searches the live web, weighs the trade-offs and brings back a plan you can actually take.",
  },
  {
    eyebrow: "The agent does the research",
    title: <>Choices become<br />a living itinerary.</>,
    copy: "It connects flights, stays, transfers and weather into one route — then explains why each option fits your time and budget.",
  },
  {
    eyebrow: "Ready when you are",
    title: <>One clear plan.<br />Nothing to stitch together.</>,
    copy: "Pick the option you like in Telegram. Trip Watch saves the itinerary and starts watching it before you even leave.",
  },
];

function TelegramScreen({ approved, onApprove }) {
  return <div className="telegram-screen" aria-label="Interactive recovery conversation demo">
    <div className="telegram-topbar"><div className="bot-avatar">T</div><div><strong>Trip Watch</strong><span>online · watching your trip</span></div><div className="topbar-menu" aria-hidden="true">•••</div></div>
    <div className="telegram-tripline">Warsaw → Munich → Lisbon · 08 Sep</div>
    {!approved ? <div className="chat-flow">
      <div className="bubble bot-bubble"><p className="bubble-title">Your Munich connection is no longer feasible.</p><p>I found a recovery option arriving <strong>2h 10m later.</strong></p></div>
      <div className="handled-card"><span>Already handled</span><ul><li>Transfer adjustment</li><li>Hotel late-arrival notice</li><li>Calendar update</li></ul></div>
      <div className="bubble bot-bubble"><p><strong>Flight change: +€34</strong></p><p className="muted-copy">Your automatic spending limit is €20.</p><button className="approve-button" onClick={() => onApprove(true)}>Approve recovery</button><button className="details-button" onClick={() => document.getElementById("watch")?.scrollIntoView({ behavior: "smooth" })}>Show details</button></div><span className="message-time">09:42</span>
    </div> : <div className="chat-flow success-flow"><div className="bubble bot-bubble"><p className="bubble-title">Trip recovered.</p><ul className="success-list"><li>Replacement itinerary selected</li><li>Transfer updated</li><li>Hotel notified</li><li>Calendar updated</li></ul><p className="muted-copy">No unresolved itinerary conflicts.</p></div><div className="bubble user-bubble">Perfect, thank you.</div><span className="message-time">09:44</span><button className="reset-button" onClick={() => onApprove(false)}>Replay demo</button></div>}
  </div>;
}

function WatchCard() {
  return <div className="watch-card"><div className="watch-card-head"><span>Lisbon via Munich</span><span className="live-dot">All clear</span></div>{watchpoints.map(([type, detail, status]) => <div className="watch-row" key={type}><span className="watch-type">{type}</span><span>{detail}</span><b>{status}</b></div>)}</div>;
}

export function App() {
  const [approved, setApproved] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [flightFrame, setFlightFrame] = useState(0);
  const [importFrame, setImportFrame] = useState(0);
  const [planningFrame, setPlanningFrame] = useState(0);
  const [planningProgress, setPlanningProgress] = useState(0);
  const flightFrameRef = useRef(0);
  const importFrameRef = useRef(0);
  const planningFrameRef = useRef(0);
  const planningProgressRef = useRef(0);
  const flightScene = flightScenes[flightFrame];
  const planningScene = planningScenes[planningFrame];
  const planningStage = planningProgress * (planningScenes.length - 1);
  const planningFrameStyle = (index) => {
    const distance = Math.min(1, Math.abs(planningStage - index));
    return {
      opacity: Math.max(0, 1 - distance),
      transform: `scale(${(1 + distance * 0.018).toFixed(3)})`,
      filter: `blur(${(distance * 0.8).toFixed(2)}px) saturate(${(1 - distance * 0.025).toFixed(3)})`,
    };
  };
  const botUrl = "https://t.me/tripagentai_bot";
  const showDemo = () => document.getElementById("demo")?.scrollIntoView({ behavior: "smooth", block: "center" });
  useEffect(() => {
    const items = [...document.querySelectorAll(".editorial-column, .story-copy, .source-copy, .source-steps, .watch-copy, .watch-card, .multimodal-copy, .multimodal-stage, .paper-visual, .recovery-copy, .compensation-copy, .policy-card, .policy-copy")];
    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduceMotion) { items.forEach((item) => item.classList.add("is-visible")); return undefined; }
    const observer = new IntersectionObserver((entries) => entries.forEach((entry) => {
      if (entry.isIntersecting) { entry.target.classList.add("is-visible"); observer.unobserve(entry.target); }
    }), { threshold: 0.18 });
    items.forEach((item) => { item.classList.add("scroll-reveal"); observer.observe(item); });
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    let animationFrame = 0;
    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduceMotion) {
      planningFrameRef.current = planningScenes.length - 1;
      planningProgressRef.current = 1;
      setPlanningFrame(planningScenes.length - 1);
      setPlanningProgress(1);
      return undefined;
    }
    planningScenes.slice(1).forEach((_, index) => {
      const image = new Image();
      image.src = `/assets/planning-film-0${index + 2}.png`;
    });
    const updatePlanningFrame = () => {
      animationFrame = 0;
      const sequence = document.querySelector(".planning-film");
      if (!sequence) return;
      const travel = Math.max(0, -sequence.getBoundingClientRect().top);
      const distance = Math.max(1, sequence.offsetHeight - window.innerHeight);
      const progress = Math.min(0.999, travel / distance);
      const nextFrame = Math.min(planningScenes.length - 1, Math.floor(progress * planningScenes.length));
      if (Math.abs(progress - planningProgressRef.current) > 0.008) {
        planningProgressRef.current = progress;
        setPlanningProgress(progress);
      }
      if (nextFrame !== planningFrameRef.current) {
        planningFrameRef.current = nextFrame;
        setPlanningFrame(nextFrame);
      }
    };
    const onScroll = () => {
      if (!animationFrame) animationFrame = window.requestAnimationFrame(updatePlanningFrame);
    };
    updatePlanningFrame();
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
    return () => {
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
      if (animationFrame) window.cancelAnimationFrame(animationFrame);
    };
  }, []);
  useEffect(() => {
    let animationFrame = 0;
    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduceMotion) {
      importFrameRef.current = 3;
      setImportFrame(3);
      return undefined;
    }
    const updateImportFrame = () => {
      animationFrame = 0;
      const section = document.querySelector(".multimodal-section");
      if (!section) return;
      const travel = Math.max(0, -section.getBoundingClientRect().top);
      const distance = Math.max(1, section.offsetHeight - window.innerHeight);
      const nextFrame = Math.min(3, Math.floor(Math.min(.999, travel / distance) * 4));
      if (nextFrame !== importFrameRef.current) {
        importFrameRef.current = nextFrame;
        setImportFrame(nextFrame);
      }
    };
    const onScroll = () => { if (!animationFrame) animationFrame = window.requestAnimationFrame(updateImportFrame); };
    updateImportFrame();
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
    return () => {
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
      if (animationFrame) window.cancelAnimationFrame(animationFrame);
    };
  }, []);
  useEffect(() => {
    let animationFrame = 0;
    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduceMotion) {
      flightFrameRef.current = 2;
      setFlightFrame(2);
      return undefined;
    }
    ["/assets/flight-film-05.png", "/assets/flight-film-06.png", "/assets/flight-film-07.png"].forEach((src) => {
      const image = new Image();
      image.src = src;
    });
    const updateFlightFrame = () => {
      animationFrame = 0;
      const sequence = document.querySelector(".flight-film");
      if (!sequence) return;
      const travel = Math.max(0, -sequence.getBoundingClientRect().top);
      const distance = Math.max(1, sequence.offsetHeight - window.innerHeight);
      const progress = Math.min(0.999, travel / distance);
      const nextFrame = Math.min(2, Math.floor(progress * 3));
      if (nextFrame !== flightFrameRef.current) {
        flightFrameRef.current = nextFrame;
        setFlightFrame(nextFrame);
      }
    };
    const onScroll = () => {
      if (!animationFrame) animationFrame = window.requestAnimationFrame(updateFlightFrame);
    };
    updateFlightFrame();
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
    return () => {
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
      if (animationFrame) window.cancelAnimationFrame(animationFrame);
    };
  }, []);
  useEffect(() => {
    let frame = 0;
    const updateClouds = () => {
      frame = 0;
      const hero = document.getElementById("top");
      if (!hero) return;
      const travelled = Math.max(0, -hero.getBoundingClientRect().top);
      const start = hero.offsetHeight * 0.28;
      const end = hero.offsetHeight * 0.98;
      const progress = Math.min(1, Math.max(0, (travelled - start) / (end - start)));
      const first = progress < 0.5 ? 1 - progress * 2 : 0;
      const second = progress < 0.5 ? progress * 2 : 2 - progress * 2;
      const third = progress < 0.5 ? 0 : progress * 2 - 1;
      hero.style.setProperty("--sky-one-opacity", first.toFixed(3));
      hero.style.setProperty("--sky-two-opacity", second.toFixed(3));
      hero.style.setProperty("--sky-three-opacity", third.toFixed(3));
    };
    const onScroll = () => {
      if (!frame) frame = window.requestAnimationFrame(updateClouds);
    };
    updateClouds();
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
    return () => {
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
      if (frame) window.cancelAnimationFrame(frame);
    };
  }, []);
  return <main>
    <section className="hero" id="top">
      <div className="hero-film-frames" aria-hidden="true"><img className="hero-film-frame sky-one" src="/assets/cinematic-sky-01.png" alt="" /><img className="hero-film-frame sky-two" src="/assets/cinematic-sky-02.png" alt="" /><img className="hero-film-frame sky-three" src="/assets/cinematic-sky-03.png" alt="" /></div>
      <nav className="nav" aria-label="Main navigation"><a className="brand" href="#top"><span className="brand-mark">✦</span><span>Trip Watch</span></a><button className="menu-toggle" aria-expanded={menuOpen} onClick={() => setMenuOpen(!menuOpen)}>Menu</button><div className={`nav-links ${menuOpen ? "open" : ""}`}><a href="#watch">Product</a><a href="#plan">Plan a trip</a><a href="#rules">Your rules</a><a href={botUrl} target="_blank" rel="noreferrer">Open bot</a></div></nav>
      <div className="hero-copy"><h1>Your travel agent<br />lives in Telegram.</h1><p>It watches your trip, handles the safe details, and only interrupts when your decision matters.</p><a className="hero-cta" href={botUrl} target="_blank" rel="noreferrer">Open Trip Watch</a></div>
      <div className="hero-screen-wrap" id="demo"><div className="hero-screen"><div className="screen-header"><span>Trip Watch · personal travel agent</span><span>Telegram</span></div><TelegramScreen approved={approved} onApprove={setApproved} /></div></div>
    </section>
    <section className="planning-film" id="plan" aria-label="Autonomous trip planning from scratch">
      <div className="planning-sticky">
        <div className="planning-frames" aria-hidden="true">
          <img className={`planning-frame ${planningFrame === 0 ? "active" : ""}`} style={planningFrameStyle(0)} src="/assets/planning-film-01.png" alt="" />
          <img className={`planning-frame ${planningFrame === 1 ? "active" : ""}`} style={planningFrameStyle(1)} src="/assets/planning-film-02.png" alt="" />
          <img className={`planning-frame ${planningFrame === 2 ? "active" : ""}`} style={planningFrameStyle(2)} src="/assets/planning-film-03.png" alt="" />
        </div>
        <div className="planning-layout">
          <div className="planning-copy" key={planningFrame}>
            <span className="eyebrow">{planningScene.eyebrow}</span>
            <h2>{planningScene.title}</h2>
            <p>{planningScene.copy}</p>
            <div className="planning-prompt" aria-label="Example trip request"><span className="planning-prompt-mark">✦</span><span>Paris · 6 nights · €600</span><span className="planning-prompt-caret" aria-hidden="true" /></div>
            <div className="planning-progress" aria-label={`Planning scene ${planningFrame + 1} of 3`}><i className={planningFrame >= 0 ? "done" : ""} /><i className={planningFrame >= 1 ? "done" : ""} /><i className={planningFrame >= 2 ? "done" : ""} /></div>
          </div>
        </div>
      </div>
    </section>
    <section className="intro-section" id="watch"><div className="editorial-column"><span className="eyebrow">A quiet watch on your trip</span><h2>Your trip deserves more than another alert.</h2><p>Trip Watch turns a disruption into a resolved plan. It follows the parts of your itinerary that affect one another — so a delayed flight is not just a delay, but a transfer, hotel and calendar problem before it becomes your problem.</p><p>When a real change is found, it checks the source, calculates the impact and works through everything your policy permits.</p></div><div className="capability-list"><div><img className="capability-icon" src="/assets/magnific-icons/flight.png" alt="" />Tracks flights, connections and airports</div><div><img className="capability-icon" src="/assets/magnific-icons/destination.png" alt="" />Watches weather that changes your route</div><div><img className="capability-icon" src="/assets/magnific-icons/car.png" alt="" />Checks hotels, transfers and activities</div><div><img className="capability-icon" src="/assets/magnific-icons/road-trip-2.png" alt="" />Explains every meaningful change</div><div><img className="capability-icon" src="/assets/magnific-icons/travel-2.png" alt="" />Uses sources you can open and verify</div><div><img className="capability-icon" src="/assets/magnific-icons/road-trip-3.png" alt="" />Keeps a complete recovery history</div></div></section>
    <section className="story-section route-section"><div className="story-copy"><span className="eyebrow">01 · Bring one itinerary</span><h2>Forward a booking.<br />Your trip becomes a living route.</h2><p>Add your flight, hotel, train, transfer or activity in the messenger. Trip Watch creates only the watchpoints that matter to this specific journey.</p><p>It is not searching the whole internet for noise. It keeps an eye on the exact people, places and connections that can change your day.</p></div><div className="paper-visual route-visual"><div className="route-cinematic" aria-label="Route impact sequence"><img src="/assets/paper-route-v2-white.png" alt="Illustrated travel route on a paper map" /><span className="route-disruption" aria-hidden="true" /></div></div></section>
    <section className="multimodal-section" id="import"><div className="multimodal-copy"><span className="eyebrow">02 · Bring anything you already have</span><h2>One forward.<br />A living itinerary.</h2><p>Send a PDF ticket, booking email, screenshot or Apple Wallet pass to Trip Watch in Telegram. Gemini reads the details in seconds — PNR, dates, terminals, connections and check-in windows.</p><p>No form. No manual data entry. Just forward what is already in your phone.</p><div className="import-types" aria-label="Supported trip sources"><span>PDF ticket</span><span>Booking email</span><span>Screenshot</span><span>.pkpass</span></div></div><div className="multimodal-stage"><div className="source-film" aria-label={`Import animation frame ${importFrame + 1} of 4`}><img className={importFrame === 0 ? "active" : ""} src="/assets/import-film-01.png" alt="PDF and travel documents entering a Telegram chat" /><img className={importFrame === 1 ? "active" : ""} src="/assets/import-film-02.png" alt="Booking email, screenshot and ticket joining the chat" /><img className={importFrame === 2 ? "active" : ""} src="/assets/import-film-03.png" alt="Travel sources collecting around the chat" /><img className={importFrame === 3 ? "active" : ""} src="/assets/import-film-04.png" alt="Connected itinerary graph resolved from the sources" /><div className="source-film-caption"><span>Forward</span><i /><span>Read</span><i /><span>Connect</span><i /><span>Ready</span></div></div></div></section>
    <section className="flight-film" id="flight" aria-label="Cinematic flight through the journey"><div className="film-sticky"><div className="film-frames" aria-hidden="true"><img className={`film-frame ${flightFrame === 0 ? "active" : ""}`} src="/assets/flight-film-05.png" alt="" /><img className={`film-frame ${flightFrame === 1 ? "active" : ""}`} src="/assets/flight-film-06.png" alt="" /><img className={`film-frame ${flightFrame === 2 ? "active" : ""}`} src="/assets/flight-film-07.png" alt="" /></div><div className="film-copy" key={flightFrame}><span>{flightScene.eyebrow}</span><h2>{flightScene.title}</h2><p>{flightScene.copy}</p><div className="film-progress" aria-label={`Flight scene ${flightFrame + 1} of 3`}><i className={flightFrame >= 0 ? "done" : ""} /><i className={flightFrame >= 1 ? "done" : ""} /><i className={flightFrame >= 2 ? "done" : ""} /></div></div></div></section>
    <section className="story-section compensation-section" id="compensation"><div className="paper-visual compensation-visual"><img src="/assets/compensation-claim-v1.png" alt="Boarding pass, verified flight timeline and a prepared compensation claim" /></div><div className="compensation-copy"><span className="eyebrow">03 · When the airline breaks the plan</span><h2>We find what you’re owed.<br />Then prepare the claim.</h2><p>When a delay or cancellation may qualify, Trip Watch checks EU 261, UK 261 or DOT rules against the route, carrier and verified timestamps.</p><p>It assembles the evidence — flight number, arrival time, boarding pass and source links — into a claim email you can review and send in one click.</p><div className="compensation-proof"><div><span>EU 261 / UK 261</span><strong>€250–€600</strong><small>potential compensation</small></div><div><span>Claim draft</span><strong>1 click</strong><small>evidence attached</small></div></div><a className="blue-cta" href={botUrl} target="_blank" rel="noreferrer">Open claim flow</a></div></section>
    <section className="policy-section autonomy-section" id="rules"><div className="policy-visual"><img src="/assets/autonomy-flow-v1.png" alt="Boarding pass, route, hotel key and calendar connected by completed travel actions" /></div><div className="policy-copy"><span className="eyebrow">04 · It acts within your rules</span><h2>The agent acts first.<br />You decide when it matters.</h2><p>Trip Watch quietly handles safe, reversible work — adjusting a transfer, notifying a hotel and updating your calendar.</p><div className="autonomy-steps"><div><span>01</span><strong>Detect</strong><small>Find the change and verify the source.</small></div><div><span>02</span><strong>Resolve</strong><small>Handle everything your policy permits.</small></div><div><span>03</span><strong>Ask</strong><small>Return only the decision that needs you.</small></div></div><a className="blue-cta" href={botUrl} target="_blank" rel="noreferrer">Open Trip Watch in Telegram</a></div></section>
    <section className="final-cta"><span className="final-mark">✦</span><h2>Travel lighter.<br />Keep the human decisions.</h2><p>Your always-on travel agent, in the messenger you already use.</p><a className="blue-cta" href={botUrl} target="_blank" rel="noreferrer">Start in Telegram</a><footer><div><strong>Trip Watch</strong><br />Built for travel that changes in real life.</div><div><a href="#watch">Product</a><a href="#rules">Safety rules</a><a href={botUrl} target="_blank" rel="noreferrer">Open bot</a></div></footer><img className="footer-landscape" src="/assets/trip-watch-footer-landscape-v1.png" alt="" /></section>
  </main>;
}
