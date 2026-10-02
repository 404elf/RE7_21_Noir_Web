"""Display-only card catalog. Wire names, types and rules stay in re7_21.py."""
# name: Chinese name, category, Chinese effect text, English effect text
CARDS = {}


def add(name, zh, category, description, english):
    CARDS[name] = (zh, category, description, english)


for number, word in enumerate(('Two', 'Three', 'Four', 'Five', 'Six', 'Seven'), 2):
    add(word, f'数字 {number}', 'draw', f'从牌堆中抽出数字 {number}。牌堆中没有这张牌时无效。', f'Draw the {number} card, if it is still in the deck.')
    add(word + '+', f'数字 {number}+', 'draw', f'获得 1 张王牌，并从牌堆中抽出数字 {number}。', f'Draw 1 trump card, then draw the {number} card if available.')
for n in (1, 2):
    add(f'Add {n}', f'加注 {n}', 'attack', f'此牌在场时，对手的赌注增加 {n}。', f'While in play, increase your opponent’s bet by {n}.')
for name, zh, n in [('Shield', '护盾', 1), ('Shield+', '护盾+', 2)]:
    add(name, zh, 'guard', f'此牌在场时，你的赌注减少 {n}。', f'While in play, reduce your bet by {n}.')
for n in (17, 24, 27):
    add(f'Go {n}', f'目标 {n}', 'target', f'本局目标点数改为 {n}。', f'change the round target to {n}.')

for row in [
    ('Destroy', '破坏', 'control', '移除对手场上最后打出的一张王牌。', 'Remove the opponent’s most recent trump.'),
    ('Destroy+', '破坏+', 'control', '移除对手所有场上王牌。', 'Remove all opponent trumps on the table.'),
    ('Destroy++', '破坏++', 'control', '移除对手场上所有王牌。此牌在场时，对手不能使用王牌。', 'Clear and lock opponent trumps.'),
    ('Return', '归还', 'control', '将你最后抽到的一张明牌放回牌堆。', 'Return your last face-up number card to the deck.'),
    ('ADD2+', '加注2+', 'attack', '将对手最后一张明牌放回牌堆。此牌在场时，对手的赌注增加 2。', 'Return the opponent’s last face-up card. While in play, raise your opponent’s bet by 2.'),
    ('Remove', '移除', 'control', '将对手最后抽到的一张明牌放回牌堆。', 'Return the opponent’s last face-up number card to the deck.'),
    ('Perfect', '完美抽牌', 'draw', '抽出对你最有利的一张牌。没有安全的牌时，抽出点数最小的一张。', 'Draw the best safe number; if none is safe, draw the smallest.'),
    ('Perfect+', '完美抽牌+', 'draw', '抽出对你最有利的一张牌。此牌在场时，对手的赌注增加 5。', 'Perform a perfect draw. While in play, raise your opponent’s bet by 5.'),
    ('Change', '交换', 'control', '交换双方最后抽到的一张明牌。', 'Swap the last face-up number cards; both players need one.'),
    ('Trump+', '王牌变换', 'resource', '消耗此牌，再随机替换 2 张手中王牌，获得 3 张新王牌。', 'Consume this trump and replace 2 other trumps with 3 new ones.'),
    ('Trump++', '王牌变换+', 'resource', '消耗此牌，随机弃 1 张王牌，再获得 4 张王牌。', 'Consume this card, discard 1 random trump, then gain 4.'),
    ('S-Attack', '盾牌攻击', 'attack', '移除你场上的 3 张护盾。此牌在场时，对手的赌注增加 3。', 'Remove 3 of your shields. While in play, raise your opponent’s bet by 3.'),
    ('S-Attack+', '盾牌攻击+', 'attack', '移除你场上的 2 张护盾。此牌在场时，对手的赌注增加 5。', 'Remove 2 of your shields. While in play, raise your opponent’s bet by 5.'),
    ('Waste', '强迫消耗', 'control', '对手使用 2 张王牌可解除；未解除则结算时失去一半王牌。', 'Opponent must play 2 trumps or lose half at settlement.'),
    ('Waste+', '强迫消耗+', 'control', '对手使用 3 张王牌可解除；未解除则结算时失去全部王牌。', 'Opponent must play 3 trumps or lose all at settlement.'),
    ('Desire', '欲望', 'attack', '此牌在场时，对手的赌注增加其手中王牌数量的一半，向下取整。', 'Raise your opponent’s bet by half their held trump cards, rounded down.'),
    ('Desire+', '欲望+', 'attack', '此牌在场时，对手的赌注增加其手中的王牌数量。', 'Raise your opponent’s bet by the number of trump cards they hold.'),
    ('Love', '爱你的敌人', 'draw', '让对手抽出最有利的一张牌。对手已爆牌或不能抽牌时无效。', 'Force a perfect draw for a non-busted opponent, unless drawing is locked.'),
    ('Gamble', '生死一搏', 'attack', '此牌在场时，双方赌注增加 100，对手不能抽牌。', 'While in play, both bets increase by 100 and your opponent cannot draw.'),
    ('D-Destroy', '死亡破坏', 'attack', '弃掉剩余王牌的一半并抽出对你最有利的一张牌。此牌在场时，对手的赌注增加 10。', 'Discard half your remaining trumps and perform a perfect draw. While in play, raise your opponent’s bet by 10.'),
    ('Add 21', '加注 21', 'attack', '此牌在场且你恰好 21 点时，对手的赌注增加 21。', 'While in play, raise your opponent’s bet by 21 if your total is exactly 21.'),
    ('Happiness', '幸福', 'resource', '双方各获得 1 张王牌。', 'Both players gain 1 trump.'),
    ('Curse', '诅咒', 'control', '随机弃 1 张剩余王牌，令未爆牌的对手抽牌堆中最大的牌。', 'Discard 1 random remaining trump; a non-busted opponent draws the largest number.'),
    ('M-Draw', '魔法抽牌', 'resource', '获得 3 张王牌。此牌在场时，你的赌注增加 1。', 'Gain 3 trumps. While in play, your bet increases by 1.'),
    ('Silence', '沉默', 'control', '此牌在场时，对手不能抽牌。', 'Lock opponent number draws.'),
    ('Oblivion', '遗忘', 'control', '跳过本局结算，清空牌桌并重新发牌。', 'Skip settlement, clear the table and deal a new round.'),
    ('Harvest', '收割', 'resource', '使用王牌后获得 1 张王牌。', 'Gain 1 trump after playing a trump.'),
    ('Escape', '逃脱', 'control', '平局结束游戏，不扣血。', 'End the game in a draw without damage.'),
    ('U-Draw', '终极抽牌', 'draw', '获得 2 张王牌，再抽出对你最有利的一张牌。', 'Gain 2 trumps, then perform a perfect draw.'),
]:
    add(*row)

CATEGORIES = {
    'attack': ('进攻', 'ATTACK', (210, 116, 99), '+'),
    'guard': ('防御', 'GUARD', (111, 170, 159), '◇'),
    'draw': ('抽牌', 'DRAW', (203, 180, 125), '↗'),
    'control': ('干扰', 'CONTROL', (161, 143, 189), '×'),
    'resource': ('资源', 'RESOURCE', (121, 156, 187), '∞'),
    'target': ('目标', 'TARGET', (203, 180, 125), '◎'),
}


def info(name):
    return CARDS.get(name, (name, 'control', '暂无这张牌的说明。', 'Card effect unavailable.'))


# Presentation aliases only; saved games, card IDs and configuration keys stay stable.
ENGLISH_NAMES = {
    'Add 1': 'One-Up', 'Add 2': 'Two-Up',
    'Perfect': 'Perfect Draw', 'Perfect+': 'Perfect Draw+',
    'Change': 'Exchange', 'Love': 'Love Your Enemy',
    'U-Draw': 'Ultimate Draw',
}

def english_name(name):
    return ENGLISH_NAMES.get(name, name)
