import mongoose from 'mongoose';

const orderSchema = new mongoose.Schema({
    userId: {type: String, required: true},
    items: {type: Array, required: true},
    amount: {type: Number, required: true},
    address: {type: Object, required: true},
    status: {type: String, default: 'Food Processing'},
    date: {type: Date, default: Date.now()},
    payment: {type: Boolean, default: false},
    paymentInfo: {
        status: { 
        type: String, 
        enum: ['succeeded', 'failed'], 
        default: undefined 
        },
        successMessage: { type: String, default: '' }, // e.g. "Payment completed successfully."
        errorCode: { type: String, default: '' },      // e.g. "card_declined"
        errorMessage: { type: String, default: '' },   // e.g. "Your card was declined."
        stripe: {
        sessionId: { type: String, default: '' },
        paymentIntentId: { type: String, default: '' },
        chargeId: { type: String, default: '' }
        },
        paidAt: { type: Date },
        failedAt: { type: Date }
    }
})

orderSchema.index({ payment: 1, "paymentInfo.status": 1, userId: 1, date: -1 });

const orderModel = mongoose.models.order || mongoose.model('order', orderSchema);
export default orderModel;