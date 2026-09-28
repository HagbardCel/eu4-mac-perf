# GOG EU IV sampler-uniform validation

One unattended A–U–A–U–A session; U suppresses duplicate assignments from the 16 verified `SShaderOpenGL::SetAll()` loop calls.

| Phase | Swaps/s | EU IV CPU ms/s | Combined W | CPU ms/swap | J/swap | Attempted | Forwarded | Suppressed |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| a1 | 52.431 | 1109.175 | 9.569 | 21.1549 | 0.18251 | 6,920,078 | 6,920,078 | 0 |
| u1 | 57.799 | 1229.21 | 10.423 | 21.267 | 0.18033 | 7,098,040 | 2,226,534 | 4,871,506 |
| a2 | 51.806 | 1103.05 | 9.491 | 21.2919 | 0.1832 | 6,560,641 | 6,560,641 | 0 |
| u2 | 51.891 | 1104.015 | 9.875 | 21.2757 | 0.1903 | 6,756,102 | 2,135,433 | 4,620,669 |
| a3 | 52.284 | 1116.34 | 9.811 | 21.3515 | 0.18765 | 6,821,413 | 6,821,413 | 0 |

Paired change versus adjacent A phases (negative means reduction):

- u1: CPU +11.13%, combined power +9.37%, swaps +10.90%, CPU/swap +0.20%, J/swap -1.38%.
- u2: CPU -0.51%, combined power +2.32%, swaps -0.30%, CPU/swap -0.22%, J/swap +2.63%.

Decision: **no_major_benefit**. Visual review: True.
Context switches by phase: {'a1': 2654, 'u1': 2724, 'a2': 2516, 'u2': 2594, 'a3': 2616}
Power estimates are system-wide; swaps are an in-process frame proxy.
