import { Router, type IRouter } from "express";
import { getTelegramReceiverStatus } from "../telegramReceiver";

const router: IRouter = Router();

router.get("/telegram/status", (_req, res) => {
  res.json(getTelegramReceiverStatus());
});

export default router;