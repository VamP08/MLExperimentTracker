import experimentService from "../services/experiment.service.js";

export const dashboard = async (req, res, next) => {
  try {
    console.log("Dashboard route accessed");

    const experiments = experimentService.getAllExperiments();

    return res.status(200).json({
      success: true,
      message: "Dashboard data retrieved successfully",
      data: experiments,
    });
  } catch (error) {
    console.error("Dashboard error:", error);
    return res.status(500).json({
      success: false,
      message: "An error occurred while fetching dashboard data",
      error: error.message,
    });
  }
};
