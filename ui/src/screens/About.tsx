import { APP_NAME, APP_VERSION, BUILT_IN_GESTURES } from "../lib/constants";
import { DaemonNotice, ScreenHeader } from "../lib/ui";
import type { ConnectionState } from "../lib/ws";

export interface AboutProps {
  connectionState: ConnectionState;
}

export function About({ connectionState }: AboutProps) {
  return (
    <>
      <ScreenHeader
        title="About"
        description="A concise guide to using AirControl safely and confidently."
      />

      {connectionState === "open" ? null : <DaemonNotice />}

      <div className="about-layout">
        <section className="panel about-product" aria-labelledby="about-product-title">
          <div>
            <span className="app-mark" aria-hidden="true">
              A
            </span>
          </div>
          <div>
            <h2 id="about-product-title">{APP_NAME}</h2>
            <p>Reliable, on-device gesture control for Windows.</p>
            <p className="version">Version {APP_VERSION}</p>
          </div>
        </section>

        <section className="panel user-guide" aria-labelledby="guide-title">
          <div className="section-heading">
            <div>
              <h2 id="guide-title">User guide</h2>
              <p>Built-in gestures are fixed safety and desktop controls.</p>
            </div>
          </div>

          <div className="guide-block" aria-labelledby="arming-title">
            <h3 id="arming-title">Arming and pausing</h3>
            <p>
              Hold an open palm to arm AirControl, or use the ARMED switch on the
              Dashboard. Hold a fist to return to IDLE. Custom mappings can fire only
              while AirControl is armed; the text label always reports the current
              state.
            </p>
          </div>

          <div className="guide-block" aria-labelledby="undo-title">
            <h3 id="undo-title">Undo</h3>
            <p>
              Within three seconds of a reversible action, choose <strong>Undo</strong>
              on the Dashboard. AirControl can switch back, reverse a scroll, or
              close Task View; hotkeys are not presented as reversible.
            </p>
          </div>

          <div className="built-in-reference">
            <h3>Built-in gesture vocabulary</h3>
            <div
              className="table-scroll"
              role="region"
              aria-label="Built-in gesture vocabulary"
              tabIndex={0}
            >
              <table>
                <thead>
                  <tr>
                    <th scope="col">Gesture</th>
                    <th scope="col">Action</th>
                    <th scope="col">How to use it</th>
                  </tr>
                </thead>
                <tbody>
                  {BUILT_IN_GESTURES.map((item) => (
                    <tr key={item.gesture}>
                      <th scope="row">{item.gesture}</th>
                      <td>{item.action}</td>
                      <td>{item.guidance}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </section>

        <section className="panel about-privacy" aria-labelledby="about-privacy-title">
          <div className="privacy-icon" aria-hidden="true">
            <svg viewBox="0 0 24 24" focusable="false">
              <path
                d="M12 3 5 6v5c0 4.7 2.8 8.1 7 10 4.2-1.9 7-5.3 7-10V6l-7-3Zm0 3.1 4 1.7V11c0 3.1-1.6 5.5-4 7-2.4-1.5-4-3.9-4-7V7.8l4-1.7Z"
                fill="currentColor"
              />
            </svg>
          </div>
          <div>
            <h2 id="about-privacy-title">Private by design</h2>
            <p>
              Camera analysis and gesture recognition run on-device. AirControl
              never stores video or camera frames; recorded gesture examples contain
              only hand-skeleton coordinates. It also keeps local mappings, settings,
              calibration metrics, and reliability statistics. Optional MediaPipe
              usage telemetry, when enabled with explicit consent, may send
              performance metadata but never camera imagery.
            </p>
          </div>
        </section>
      </div>
    </>
  );
}

export default About;
