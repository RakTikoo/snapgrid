# Security policy

## Reporting a problem

Please do not open a public issue for a security problem.

Use GitHub's private vulnerability reporting: on this repository's **Security** tab, choose **Report a vulnerability**. If that option is not available to you, open an issue titled "Security contact request" with no details in it, and a private route will be arranged.

## Scope

snapgrid runs locally on image files you give it; it makes no network calls at run time. A security problem here would be something a crafted image file can make the tool do beyond reading that file and writing the requested outputs — for example a crash that corrupts memory in an image library, or writing outside the requested output path.
