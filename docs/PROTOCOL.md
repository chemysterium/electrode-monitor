# Serial protocol reference

This client targets the command tree in manual **8.716.1013**, sections 3.1, 3.2.2.51, 3.2.2.72, 3.2.2.80, and 3.3. Its compatibility check accepts firmware `716.<digits>`.

[Original product manual, hosted by its publisher](https://www.metrohm.com/content/dam/metrohm/shared/documents/manuals/87/87161013.pdf)

This link is a technical citation. The application does not display publisher branding or redistribute its document or artwork.

| Purpose | Command before CR LF | Section |
|---|---|---|
| Firmware | `&Config.Aux.Prog $Q` | 3.2.2.51 |
| Operating status | `$D` | 3.1.3 |
| Measurement state | `&Assembly.Meas.Status $Q` | 3.2.2.80 |
| Electrode input | `&Assembly.Meas.MeasInput $Q` | 3.2.2.80 |
| Enable measurement | `&Assembly.Meas.Status "ON"` | 3.2.2.80 |
| Disable measurement | `&Assembly.Meas.Status "OFF"` | 3.2.2.80 |
| Raw voltage | `&Info.ActualInfo.Assembly.Meas $Q` | 3.2.2.72 |

Commands are ASCII with CR LF termination; replies end with CR CR LF. Full-root paths avoid dependence on the previous node. State changes are followed by a state query on the same line, separated by a semicolon.

The PC uses 9600/8/N/1 with RTS and DTR held active. Automatic RTS/CTS and XON/XOFF are disabled for the verified cable. The app does not rewrite instrument serial settings.

Start requires idle status and voltage input 1, 2, or Diff. Every reading checks measurement ON and unchanged input. Invalid/overrange values are rejected. Timeout or unexpected extra data closes the connection to avoid misattributing a stale reply.

Start controls only separate measurement, which blocks method starts. No titration, dosing, stirring, or calibration commands are issued. Stop turns measurement OFF only if the app owns it. After connection loss, check the instrument locally.
