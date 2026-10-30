export async function register() {
  if (process.env.NEXT_RUNTIME === "nodejs") {
    const { validateStartup } = await import("./features/auth/startup");
    try {
      validateStartup();
    } catch (error) {
      // Next reports hook failures without terminating its listener. Fail the process.
      console.error(
        JSON.stringify({
          event: "configuration_rejected",
          reason:
            error instanceof Error
              ? error.message
              : "Invalid startup configuration",
        }),
      );
      process.exit(1);
    }
  }
}
